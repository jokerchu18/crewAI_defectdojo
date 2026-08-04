"""Knowledge store access for read-only CrewAI search tools."""

import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, Field

from defectdojo_crewai.config.settings import hybrid_search_source_types, settings
from defectdojo_crewai.knowledge.storage import (
    SOURCE_TYPES,
    KnowledgeStore,
    build_knowledge_store,
)


_STORE: KnowledgeStore | None = None
_LIBRARY_FINGERPRINT: str | None = None
_FINGERPRINT_MTIME: float = 0.0
_STORE_LOCK = Lock()
LOGGER = logging.getLogger(__name__)

# ── Embedding cache ─────────────────────────────────────────────────────
# Avoid recomputing the same query embedding on every tool call.
# Sized to cover typical agent sessions; LRU eviction keeps memory bounded.

_EMBEDDING_CACHE: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
_EMBEDDING_CACHE_MAX = 256
_EMBEDDING_CACHE_LOCK = Lock()

# ── Search-result cache (short TTL) ─────────────────────────────────────
# When the same (query, source_type, k) tuple is searched within a brief
# window, return the cached result instead of hitting Qdrant again.
# This is especially useful when an agent makes back-to-back tool calls.

_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_SEARCH_CACHE_TTL_SECONDS = 10.0
_SEARCH_CACHE_MAX = 128
_SEARCH_CACHE_LOCK = Lock()


class KnowledgeSearchOutcome(BaseModel):
    status: Literal["matched", "no_match", "unavailable"]
    matches: list[dict[str, Any]] = Field(default_factory=list)
    best_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    error_type: str | None = None


def search_knowledge(
    *,
    query: str,
    source_types: Iterable[str] | None = None,
    filters: dict[str, str | int | bool] | None = None,
    k: int = 4,
    min_similarity: float | None = None,
) -> list[dict[str, Any]]:
    """Search a scoped logical partition without mutating an Agent prompt."""
    t_total = time.perf_counter()

    resolved_types = tuple(sorted(source_types or SOURCE_TYPES))
    cache_key = _search_cache_key(
        query=query,
        source_types=resolved_types,
        filters=filters,
        k=k,
        min_similarity=min_similarity,
    )

    # ── short-TTL result cache ──────────────────────────────────────
    t_cache = time.perf_counter()
    with _SEARCH_CACHE_LOCK:
        entry = _SEARCH_CACHE.get(cache_key)
        if entry is not None:
            cached_at, cached_result = entry
            if time.monotonic() - cached_at < _SEARCH_CACHE_TTL_SECONDS:
                elapsed = (time.perf_counter() - t_total) * 1000
                LOGGER.info(
                    "search_knowledge RESULT_CACHE_HIT | query=%.60s | %.1fms",
                    query, elapsed,
                )
                return list(cached_result)
            del _SEARCH_CACHE[cache_key]
    t_cache_ms = (time.perf_counter() - t_cache) * 1000

    # ── get store (lazy init + throttled fingerprint) ───────────────
    t_store = time.perf_counter()
    store = _get_store()
    t_store_ms = (time.perf_counter() - t_store) * 1000

    # ── embed query (cached or API call) ────────────────────────────
    t_embed = time.perf_counter()
    dense_vector = _cached_embed_query(store, query)
    t_embed_ms = (time.perf_counter() - t_embed) * 1000

    # ── Qdrant search ───────────────────────────────────────────────
    t_qdrant = time.perf_counter()
    result = store.search(
        query=query,
        source_types=list(resolved_types),
        filters=filters,
        k=k,
        min_similarity=min_similarity,
        dense_vector_override=dense_vector,
    )
    t_qdrant_ms = (time.perf_counter() - t_qdrant) * 1000

    # ── populate result cache ───────────────────────────────────────
    with _SEARCH_CACHE_LOCK:
        if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX:
            oldest = next(iter(_SEARCH_CACHE))
            del _SEARCH_CACHE[oldest]
        _SEARCH_CACHE[cache_key] = (time.monotonic(), result)

    total_ms = (time.perf_counter() - t_total) * 1000
    LOGGER.info(
        "search_knowledge TIMING | query=%.60s | total=%.1fms "
        "cache_check=%.1fms get_store=%.1fms embed=%.1fms qdrant=%.1fms | "
        "results=%d",
        query, total_ms, t_cache_ms, t_store_ms, t_embed_ms, t_qdrant_ms,
        len(result),
    )
    return result


def _search_cache_key(
    *,
    query: str,
    source_types: tuple[str, ...],
    filters: dict[str, str | int | bool] | None,
    k: int,
    min_similarity: float | None,
) -> str:
    """Stable cache key for a search call."""
    filter_items = tuple(sorted((filters or {}).items()))
    return (
        f"{query}|{source_types}|{filter_items}|{k}|"
        f"{min_similarity:.3f}" if min_similarity is not None
        else f"{query}|{source_types}|{filter_items}|{k}|None"
    )


def _cached_embed_query(store: KnowledgeStore, query: str) -> list[float] | None:
    """Return a cached dense embedding for *query*, or compute and cache it."""
    t0 = time.perf_counter()
    with _EMBEDDING_CACHE_LOCK:
        entry = _EMBEDDING_CACHE.get(query)
        if entry is not None:
            _EMBEDDING_CACHE.move_to_end(query)
            elapsed = (time.perf_counter() - t0) * 1000
            LOGGER.info(
                "embed_cache HIT  | query=%.60s | %.1fms", query, elapsed,
            )
            return entry[1]

    # Not in cache — compute via the store's embedder.
    t_embed = time.perf_counter()
    try:
        vector = store.embeddings.embed_query(query)
    except Exception:
        LOGGER.debug("Failed to pre-compute query embedding; will use inline path.")
        return None
    t_embed_ms = (time.perf_counter() - t_embed) * 1000

    with _EMBEDDING_CACHE_LOCK:
        if query in _EMBEDDING_CACHE:
            _EMBEDDING_CACHE.move_to_end(query)
            return _EMBEDDING_CACHE[query][1]
        while len(_EMBEDDING_CACHE) >= _EMBEDDING_CACHE_MAX:
            _EMBEDDING_CACHE.popitem(last=False)
        _EMBEDDING_CACHE[query] = (time.monotonic(), vector)

    total_ms = (time.perf_counter() - t0) * 1000
    LOGGER.info(
        "embed_cache MISS | query=%.60s | embed_api=%.1fms total=%.1fms",
        query, t_embed_ms, total_ms,
    )
    return vector


def search_knowledge_safely(
    *,
    query: str,
    source_types: Iterable[str] | None = None,
    filters: dict[str, str | int | bool] | None = None,
    k: int = 4,
    min_similarity: float | None = None,
) -> KnowledgeSearchOutcome:
    """Return an auditable retrieval outcome without failing the workflow."""
    if not settings.knowledge_enabled:
        return KnowledgeSearchOutcome(
            status="unavailable",
            error_type="KnowledgeDisabled",
        )

    try:
        matches = search_knowledge(
            query=query,
            source_types=source_types,
            filters=filters,
            k=k,
            min_similarity=min_similarity,
        )
    except Exception as exc:
        LOGGER.exception("Knowledge retrieval is unavailable")
        return KnowledgeSearchOutcome(
            status="unavailable",
            error_type=type(exc).__name__,
        )

    if not matches:
        return KnowledgeSearchOutcome(status="no_match")

    scores = [
        float(match["score"])
        for match in matches
        if isinstance(match.get("score"), int | float)
    ]
    return KnowledgeSearchOutcome(
        status="matched",
        matches=matches,
        best_similarity=max(0.0, min(1.0, max(scores))) if scores else None,
    )


def get_knowledge_store() -> KnowledgeStore:
    return _get_store()


def _get_store() -> KnowledgeStore:
    global _STORE, _LIBRARY_FINGERPRINT, _FINGERPRINT_MTIME

    t0 = time.perf_counter()

    with _STORE_LOCK:
        if _STORE is None:
            t_init = time.perf_counter()
            sparse_embedder = _build_sparse_embedder() if settings.hybrid_search_enabled else None
            source_types = hybrid_search_source_types()
            _STORE = build_knowledge_store(
                embedding_provider=settings.embedding_provider,
                embedding_model=settings.embedding_model,
                embedding_dimensions=settings.embedding_dimensions,
                embedding_cache_dir=settings.embedding_cache_dir,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                qdrant_url=settings.qdrant_url,
                qdrant_api_key=settings.qdrant_api_key,
                qdrant_collection_name=settings.qdrant_collection_name,
                qdrant_timeout_seconds=settings.qdrant_timeout_seconds,
                qdrant_prefer_grpc=settings.qdrant_prefer_grpc,
                sparse_embedder=sparse_embedder,
                hybrid_source_types=source_types,
            )
            t_init_ms = (time.perf_counter() - t_init) * 1000
            LOGGER.info(
                "get_store INIT | provider=%s model=%s dims=%d grpc=%s | "
                "build_knowledge_store=%.1fms",
                settings.embedding_provider, settings.embedding_model,
                settings.embedding_dimensions, settings.qdrant_prefer_grpc,
                t_init_ms,
            )

    t_fp = time.perf_counter()
    _now = time.monotonic()
    if _now - _FINGERPRINT_MTIME >= 30.0:
        fingerprint = _knowledge_fingerprint(settings.knowledge_base_dir)
        _FINGERPRINT_MTIME = _now
        if fingerprint != _LIBRARY_FINGERPRINT:
            t_sync = time.perf_counter()
            changed = _STORE.sync_markdown_library(settings.knowledge_base_dir)
            t_sync_ms = (time.perf_counter() - t_sync) * 1000
            LOGGER.info(
                "get_store SYNC | %d chunks indexed | %.1fms", changed, t_sync_ms,
            )
            _LIBRARY_FINGERPRINT = fingerprint
    t_fp_ms = (time.perf_counter() - t_fp) * 1000

    total_ms = (time.perf_counter() - t0) * 1000
    if total_ms > 5:
        LOGGER.info(
            "get_store TIMING | total=%.1fms fp_check=%.1fms", total_ms, t_fp_ms,
        )

    return _STORE


def _build_sparse_embedder():
    """Create a SparseEmbedder using the configured TEI endpoint."""
    base_url = settings.openai_base_url
    if not base_url:
        base_url = "http://localhost:8081/v1"
    from defectdojo_crewai.knowledge.sparse_embedder import SparseEmbedder

    return SparseEmbedder(
        tei_base_url=base_url,
        api_key=settings.openai_api_key,
        timeout_seconds=float(settings.qdrant_timeout_seconds),
    )


def _knowledge_fingerprint(doc_dir: Path) -> str:
    if not doc_dir.is_dir():
        raise FileNotFoundError(
            f"Knowledge base directory does not exist: {doc_dir}"
        )
    documents = []
    for path in sorted(doc_dir.rglob("*.md")):
        if not path.is_file():
            continue
        st = path.stat()
        documents.append(
            (
                str(path.relative_to(doc_dir)),
                st.st_mtime_ns,
                st.st_size,
            )
        )
    return hashlib.sha256(
        json.dumps(documents, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
