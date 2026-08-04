"""Qdrant-backed knowledge storage with logical source-type partitions."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

# ── Pre-import heavy packages ──────────────────────────────────────────
# The first instantiation of OpenAIEmbeddings / QdrantClient triggers
# lazy imports of the `openai` and `qdrant_client` packages (~3-4 s each).
# Force them here so the cost is paid once at module-load time, not on the
# first user-facing knowledge tool call.
import openai  # noqa: F401  ≈3 s
import qdrant_client  # noqa: F401  ≈1 s

from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from defectdojo_crewai.utils.retry import execute_with_resilience  # noqa: E402
from defectdojo_crewai.utils.timeout_configs import get_timeout_config  # noqa: E402

# Transient network / connection failures worth retrying for Qdrant.
_RETRYABLE_QDRANT: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)

LOGGER = logging.getLogger(__name__)

SOURCE_LIBRARY = "library"
SOURCE_AUDIT = "audit"
SOURCE_TRIAGE = "triage"
SOURCE_REMEDIATION = "remediation"
SOURCE_TYPES = {
    SOURCE_LIBRARY,
    SOURCE_AUDIT,
    SOURCE_TRIAGE,
    SOURCE_REMEDIATION,
}
_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)


class KnowledgeStore:
    """Store and retrieve dense vectors from one Qdrant collection.

    When *sparse_embedder* is provided, the collection is upgraded with a
    named ``"sparse"`` vector and ``search()`` uses reciprocal-rank-fusion
    (RRF) for any source type listed in *hybrid_source_types*.
    """

    _SPARSE_VECTOR_NAME = "sparse"
    _DEFAULT_PREFETCH_LIMIT = 20

    def __init__(
        self,
        *,
        embeddings: Embeddings,
        qdrant_url: str,
        qdrant_api_key: str | None,
        qdrant_collection_name: str,
        qdrant_timeout_seconds: int,
        qdrant_prefer_grpc: bool,
        embedding_dimensions: int,
        embedding_model: str,
        sparse_embedder: object | None = None,
        hybrid_source_types: frozenset[str] = frozenset(),
    ) -> None:
        t0 = time.perf_counter()
        self.embeddings = embeddings
        self.collection_name = qdrant_collection_name
        self.embedding_model = embedding_model
        self.sparse_embedder = sparse_embedder
        self.hybrid_source_types = hybrid_source_types
        t_client = time.perf_counter()
        self.client = QdrantClient(
            url=qdrant_url,
            api_key=qdrant_api_key or None,
            timeout=qdrant_timeout_seconds,
            prefer_grpc=qdrant_prefer_grpc,
        )
        t_client_ms = (time.perf_counter() - t_client) * 1000
        t_coll = time.perf_counter()
        self._ensure_collection(embedding_dimensions)
        t_coll_ms = (time.perf_counter() - t_coll) * 1000
        total_ms = (time.perf_counter() - t0) * 1000
        LOGGER.info(
            "KnowledgeStore.__init__ | client=%.1fms ensure_coll=%.1fms total=%.1fms",
            t_client_ms, t_coll_ms, total_ms,
        )

    def close(self) -> None:
        self.client.close()

    def upsert_texts(
        self,
        *,
        texts: Sequence[str],
        source_type: str,
        source_id: str,
        metadata: Sequence[dict] | None = None,
        point_ids: Sequence[str] | None = None,
    ) -> list[str]:
        _validate_source_type(source_type)
        if not texts:
            return []
        if metadata is not None and len(metadata) != len(texts):
            raise ValueError("Metadata count must match text count.")

        vectors = self.embeddings.embed_documents(list(texts))
        sparse_vectors: list[dict | None] = []
        if self.sparse_embedder is not None:
            try:
                sparse_vectors = self.sparse_embedder.embed(list(texts))
            except Exception:
                LOGGER.exception(
                    "Failed to compute sparse vectors; falling back to "
                    "dense-only for this batch."
                )
                sparse_vectors = [None] * len(texts)
        else:
            sparse_vectors = [None] * len(texts)

        if len(sparse_vectors) != len(texts):
            sparse_vectors = [None] * len(texts)

        point_ids = list(point_ids or [
            _point_id(source_type, source_id, index)
            for index in range(len(texts))
        ])
        if len(point_ids) != len(texts):
            raise ValueError("Point ID count must match text count.")

        points = []
        for index, (text, vector, point_id) in enumerate(
            zip(texts, vectors, point_ids, strict=True)
        ):
            item_metadata = dict(metadata[index]) if metadata else {}
            payload = {
                "source_type": source_type,
                "source_id": source_id,
                "text": text,
                "content_hash": _content_hash(text),
                "embedding_model": self.embedding_model,
                **item_metadata,
            }
            point_vector = _build_point_vector(
                dense_vector=vector,
                sparse_dict=(
                    sparse_vectors[index]
                    if index < len(sparse_vectors)
                    else None
                ),
                sparse_vector_name=self._SPARSE_VECTOR_NAME,
            )
            points.append(
                PointStruct(
                    id=point_id,
                    vector=point_vector,
                    payload=payload,
                )
            )
        collection = self.collection_name

        def _upsert() -> list[str]:
            self.client.upsert(
                collection_name=collection,
                points=points,
                wait=True,
            )
            return point_ids

        return execute_with_resilience(
            "qdrant",
            get_timeout_config("qdrant"),
            _upsert,
            retryable=_RETRYABLE_QDRANT,
        )

    def search(
        self,
        *,
        query: str,
        source_types: Iterable[str] | None = None,
        filters: dict[str, str | int | bool] | None = None,
        k: int = 4,
        min_similarity: float | None = None,
        dense_vector_override: list[float] | None = None,
        sparse_vector_override: dict[str, Any] | None = None,
    ) -> list[dict]:
        if not query.strip():
            return []
        requested_source_types = list(source_types or SOURCE_TYPES)
        for source_type in requested_source_types:
            _validate_source_type(source_type)

        conditions = [
            FieldCondition(
                key="source_type",
                match=MatchAny(any=requested_source_types),
            )
        ]
        for key, value in (filters or {}).items():
            conditions.append(
                FieldCondition(key=key, match=MatchValue(value=value))
            )

        use_hybrid = (
            self.sparse_embedder is not None
            and bool(set(requested_source_types) & self.hybrid_source_types)
        )
        if use_hybrid:
            return self._hybrid_search(
                query=query,
                query_filter=Filter(must=conditions),
                k=k,
                min_similarity=min_similarity,
                dense_vector_override=dense_vector_override,
                sparse_vector_override=sparse_vector_override,
            )

        # Use pre-computed vector when available; otherwise compute inline.
        t_embed = time.perf_counter()
        query_vector = dense_vector_override
        if query_vector is None:
            query_vector = self.embeddings.embed_query(query)
        t_embed_ms = (time.perf_counter() - t_embed) * 1000

        query_filter = Filter(must=conditions)
        collection = self.collection_name
        limit = max(1, k)

        t_api = time.perf_counter()
        result = execute_with_resilience(
            "qdrant",
            get_timeout_config("qdrant"),
            lambda: self._query_points(
                collection, query_vector, query_filter, limit, min_similarity,
            ),
            retryable=_RETRYABLE_QDRANT,
        )
        t_api_ms = (time.perf_counter() - t_api) * 1000
        LOGGER.info(
            "store.search DENSE | query=%.60s | embed=%.1fms qdrant_api=%.1fms | "
            "results=%d",
            query, t_embed_ms, t_api_ms, len(result),
        )

        return result

    def _query_points(
        self,
        collection: str,
        query_vector: list[float],
        query_filter: Filter,
        limit: int,
        min_similarity: float | None,
    ) -> list[dict]:
        resp = self.client.query_points(
            collection_name=collection,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=min_similarity,
            with_payload=True,
            with_vectors=False,
        )
        return self._transform_points(resp.points)

    def _hybrid_search(
        self,
        *,
        query: str,
        query_filter: Filter,
        k: int,
        min_similarity: float | None,
        dense_vector_override: list[float] | None = None,
        sparse_vector_override: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Dense + sparse hybrid retrieval with reciprocal-rank fusion."""
        t0 = time.perf_counter()

        t_dense = time.perf_counter()
        dense_vector: list[float] = (
            dense_vector_override or self.embeddings.embed_query(query)
        )
        t_dense_ms = (time.perf_counter() - t_dense) * 1000

        # Fetch extra candidates per shard — RRF merges from both sets.
        prefetch_limit = max(k, self._DEFAULT_PREFETCH_LIMIT)

        prefetch_queries = [
            Prefetch(
                query=dense_vector,
                filter=query_filter,
                limit=prefetch_limit,
            ),
        ]

        t_sparse = time.perf_counter()
        if sparse_vector_override and sparse_vector_override.get("indices") and sparse_vector_override.get("values"):
            prefetch_queries.append(
                Prefetch(
                    query=SparseVector(
                        indices=sparse_vector_override["indices"],
                        values=sparse_vector_override["values"],
                    ),
                    using=self._SPARSE_VECTOR_NAME,
                    filter=query_filter,
                    limit=prefetch_limit,
                )
            )
        else:
            try:
                sparse_vectors = self.sparse_embedder.embed([query])
            except Exception:
                LOGGER.exception(
                    "Sparse query embedding failed; falling back to dense-only."
                )
                sparse_vectors = []

            if sparse_vectors:
                sparse_vec = sparse_vectors[0]
                if sparse_vec.get("indices") and sparse_vec.get("values"):
                    prefetch_queries.append(
                        Prefetch(
                            query=SparseVector(
                                indices=sparse_vec["indices"],
                                values=sparse_vec["values"],
                            ),
                            using=self._SPARSE_VECTOR_NAME,
                            filter=query_filter,
                            limit=prefetch_limit,
                        )
                    )
        t_sparse_ms = (time.perf_counter() - t_sparse) * 1000

        collection = self.collection_name
        fusion_limit = max(1, k)

        t_api = time.perf_counter()
        result = execute_with_resilience(
            "qdrant",
            get_timeout_config("qdrant"),
            lambda: self._query_hybrid_points(
                collection, prefetch_queries, fusion_limit, min_similarity,
            ),
            retryable=_RETRYABLE_QDRANT,
        )
        t_api_ms = (time.perf_counter() - t_api) * 1000

        total_ms = (time.perf_counter() - t0) * 1000
        LOGGER.info(
            "store.search HYBRID | query=%.60s | dense=%.1fms sparse=%.1fms "
            "qdrant_api=%.1fms total=%.1fms | results=%d",
            query, t_dense_ms, t_sparse_ms, t_api_ms, total_ms, len(result),
        )
        return result

    def _query_hybrid_points(
        self,
        collection: str,
        prefetch_queries: list,
        fusion_limit: int,
        min_similarity: float | None,
    ) -> list[dict]:
        resp = self.client.query_points(
            collection_name=collection,
            prefetch=prefetch_queries,
            query=FusionQuery(fusion=Fusion.RRF),
            limit=fusion_limit,
            with_payload=True,
            with_vectors=False,
        )
        matches = self._transform_points(resp.points)
        if min_similarity is not None:
            matches = [
                match
                for match in matches
                if float(match["score"]) >= min_similarity
            ]
        return matches

    @staticmethod
    def _transform_points(points: list) -> list[dict]:
        matches = []
        for point in points:
            payload = dict(point.payload or {})
            matches.append(
                {
                    "content": str(payload.pop("text", "")),
                    "metadata": payload,
                    "score": float(point.score),
                }
            )
        return matches

    def sync_markdown_library(self, doc_dir: str | Path) -> int:
        """Incrementally upsert changed Markdown chunks and delete stale chunks."""
        base_path = Path(doc_dir)
        if not base_path.is_dir():
            raise FileNotFoundError(
                f"Knowledge base directory does not exist: {base_path}"
            )

        desired: dict[str, tuple[str, dict]] = {}
        for file_path in sorted(base_path.rglob("*.md")):
            text = file_path.read_text(encoding="utf-8")
            if not text.strip():
                continue
            source = str(file_path.relative_to(base_path))
            for index, chunk in enumerate(_SPLITTER.split_text(text)):
                point_id = _point_id(SOURCE_LIBRARY, source, index)
                desired[point_id] = (
                    chunk,
                    {
                        "source": source,
                        "chunk_index": index,
                        "document_path": source,
                    },
                )
        if not desired:
            raise ValueError(
                f"No non-empty Markdown files found in: {base_path}"
            )

        existing = self._points_for_source_type(SOURCE_LIBRARY)
        stale_ids = set(existing) - set(desired)
        if stale_ids:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=PointIdsList(points=list(stale_ids)),
                wait=True,
            )

        changed_ids = [
            point_id
            for point_id, (text, _) in desired.items()
            if existing.get(point_id, {}).get("content_hash") != _content_hash(text)
            or existing.get(point_id, {}).get("embedding_model")
            != self.embedding_model
        ]
        if changed_ids:
            self.upsert_texts(
                texts=[desired[point_id][0] for point_id in changed_ids],
                source_type=SOURCE_LIBRARY,
                source_id="markdown-library",
                metadata=[desired[point_id][1] for point_id in changed_ids],
                point_ids=changed_ids,
            )
        return len(desired)

    def _ensure_collection(self, embedding_dimensions: int) -> None:
        t0 = time.perf_counter()

        t_exists = time.perf_counter()
        exists = self.client.collection_exists(self.collection_name)
        t_exists_ms = (time.perf_counter() - t_exists) * 1000

        if exists:
            t_get = time.perf_counter()
            collection = self.client.get_collection(self.collection_name)
            t_get_ms = (time.perf_counter() - t_get) * 1000

            vectors = collection.config.params.vectors
            current_size = (
                vectors[""].size
                if isinstance(vectors, dict)
                else vectors.size
            )
            if current_size != embedding_dimensions:
                raise ValueError(
                    f"Collection {self.collection_name!r} uses {current_size} "
                    f"dimensions, but {self.embedding_model} requires "
                    f"{embedding_dimensions}. Rebuild the collection before "
                    "starting the application."
                )
            t_sparse = time.perf_counter()
            self._ensure_sparse_vector_config(collection)
            t_sparse_ms = (time.perf_counter() - t_sparse) * 1000
        else:
            t_create = time.perf_counter()
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )
            t_create_ms = (time.perf_counter() - t_create) * 1000
            t_get_ms = 0.0
            t_sparse = time.perf_counter()
            self._ensure_sparse_vector_config(None)
            t_sparse_ms = (time.perf_counter() - t_sparse) * 1000
            LOGGER.info(
                "_ensure_collection CREATE | exists=%.1fms create=%.1fms "
                "sparse=%.1fms",
                t_exists_ms, t_create_ms, t_sparse_ms,
            )

        t_index = time.perf_counter()
        for field_name in ("source_type", "intent", "severity", "cwe_id"):
            try:
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                    wait=False,
                )
            except Exception:
                LOGGER.debug(
                    "Payload index already exists or cannot be created: %s",
                    field_name,
                    exc_info=True,
                )
        t_index_ms = (time.perf_counter() - t_index) * 1000

        total_ms = (time.perf_counter() - t0) * 1000
        LOGGER.info(
            "_ensure_collection TIMING | exists=%.1fms get_coll=%.1fms "
            "sparse=%.1fms indexes=%.1fms | total=%.1fms",
            t_exists_ms, t_get_ms, t_sparse_ms, t_index_ms, total_ms,
        )

    def _ensure_sparse_vector_config(self, collection: object | None) -> None:
        """Add named sparse vector to the collection when hybrid is enabled."""
        if self.sparse_embedder is None:
            return

        existing_sparse: dict[str, object] = {}
        if collection is not None:
            sparse_config = getattr(
                collection.config.params, "sparse_vectors", None
            )
            if sparse_config is not None:
                existing_sparse = dict(sparse_config)

        if self._SPARSE_VECTOR_NAME in existing_sparse:
            return  # already configured

        LOGGER.info(
            "Adding sparse vector %r to collection %r for hybrid search.",
            self._SPARSE_VECTOR_NAME,
            self.collection_name,
        )
        self.client.update_collection(
            collection_name=self.collection_name,
            sparse_vectors_config={
                self._SPARSE_VECTOR_NAME: SparseVectorParams(),
            },
        )

    def _points_for_source_type(self, source_type: str) -> dict[str, dict]:
        points: dict[str, dict] = {}
        offset = None
        while True:
            batch, offset = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="source_type",
                            match=MatchValue(value=source_type),
                        )
                    ]
                ),
                limit=256,
                with_payload=True,
                with_vectors=False,
                offset=offset,
            )
            for point in batch:
                points[str(point.id)] = dict(point.payload or {})
            if offset is None:
                return points


def build_knowledge_store(
    *,
    embedding_provider: str,
    embedding_model: str,
    embedding_dimensions: int,
    embedding_cache_dir: str | Path,
    api_key: str,
    base_url: str | None,
    qdrant_url: str,
    qdrant_api_key: str | None,
    qdrant_collection_name: str,
    qdrant_timeout_seconds: int,
    qdrant_prefer_grpc: bool,
    sparse_embedder: object | None = None,
    hybrid_source_types: frozenset[str] = frozenset(),
) -> KnowledgeStore:
    return KnowledgeStore(
        embeddings=_build_embeddings(
            provider=embedding_provider,
            model=embedding_model,
            cache_dir=embedding_cache_dir,
            api_key=api_key,
            base_url=base_url,
        ),
        qdrant_url=qdrant_url,
        qdrant_api_key=qdrant_api_key,
        qdrant_collection_name=qdrant_collection_name,
        qdrant_timeout_seconds=qdrant_timeout_seconds,
        qdrant_prefer_grpc=qdrant_prefer_grpc,
        embedding_dimensions=embedding_dimensions,
        embedding_model=embedding_model,
        sparse_embedder=sparse_embedder,
        hybrid_source_types=hybrid_source_types,
    )


def _build_embeddings(
    *,
    provider: str,
    model: str,
    cache_dir: str | Path,
    api_key: str,
    base_url: str | None,
) -> Embeddings:
    t0 = time.perf_counter()
    if provider == "fastembed":
        result = FastEmbedEmbeddings(
            model_name=model,
            cache_dir=str(cache_dir),
        )
        LOGGER.info(
            "_build_embeddings fastembed | model=%s | %.1fms",
            model, (time.perf_counter() - t0) * 1000,
        )
        return result
    if provider in {"openai", "tei"}:
        options = {
            "api_key": api_key,
            "model": model,
            "max_retries": 1,
        }
        if base_url:
            options["base_url"] = base_url
        if provider == "tei":
            options["check_embedding_ctx_length"] = False
        result = OpenAIEmbeddings(**options)
        LOGGER.info(
            "_build_embeddings %s | model=%s base_url=%s | %.1fms",
            provider, model, base_url, (time.perf_counter() - t0) * 1000,
        )
        return result
    raise ValueError(f"Unsupported embedding provider: {provider}")


def _point_id(source_type: str, source_id: str, chunk_index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"{source_type}:{source_id}:{chunk_index}"))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_source_type(source_type: str) -> None:
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"Unsupported knowledge source type: {source_type}")


def _build_point_vector(
    *,
    dense_vector: list[float],
    sparse_dict: dict | None,
    sparse_vector_name: str,
) -> list[float] | dict[str, list[float] | SparseVector]:
    """Return a point vector payload for Qdrant PointStruct.

    When *sparse_dict* is None the return is a plain dense list (backward
    compatible with unnamed vectors).  Otherwise the return is a dict with
    the unnamed dense vector (``""`` key) and the named sparse vector.
    """
    if sparse_dict is None:
        return dense_vector
    indices = sparse_dict.get("indices")
    values = sparse_dict.get("values")
    if not indices or not values:
        return dense_vector
    return {
        "": dense_vector,
        sparse_vector_name: SparseVector(indices=list(indices), values=list(values)),
    }
