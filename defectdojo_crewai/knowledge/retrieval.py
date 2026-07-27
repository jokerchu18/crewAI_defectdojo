"""Knowledge store access for read-only CrewAI search tools."""

import hashlib
import json
import logging
from collections.abc import Iterable
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, Field

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.knowledge.storage import (
    SOURCE_TYPES,
    KnowledgeStore,
    build_knowledge_store,
)


_STORE: KnowledgeStore | None = None
_LIBRARY_FINGERPRINT: str | None = None
_STORE_LOCK = Lock()
LOGGER = logging.getLogger(__name__)


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
    return _get_store().search(
        query=query,
        source_types=source_types or SOURCE_TYPES,
        filters=filters,
        k=k,
        min_similarity=min_similarity,
    )


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
    global _STORE, _LIBRARY_FINGERPRINT

    fingerprint = _knowledge_fingerprint(settings.knowledge_base_dir)
    with _STORE_LOCK:
        if _STORE is None:
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
            )
        if fingerprint != _LIBRARY_FINGERPRINT:
            _STORE.sync_markdown_library(settings.knowledge_base_dir)
            _LIBRARY_FINGERPRINT = fingerprint
    return _STORE


def _knowledge_fingerprint(doc_dir: Path) -> str:
    if not doc_dir.is_dir():
        raise FileNotFoundError(
            f"Knowledge base directory does not exist: {doc_dir}"
        )
    documents = [
        (
            str(path.relative_to(doc_dir)),
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in sorted(doc_dir.rglob("*.md"))
        if path.is_file()
    ]
    return hashlib.sha256(
        json.dumps(documents, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
