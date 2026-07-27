"""Qdrant-backed knowledge storage with logical source-type partitions."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointIdsList,
    PointStruct,
    PayloadSchemaType,
    VectorParams,
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
    """Store and retrieve dense vectors from one Qdrant collection."""

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
    ) -> None:
        self.embeddings = embeddings
        self.collection_name = qdrant_collection_name
        self.embedding_model = embedding_model
        self.client = QdrantClient(
            url=qdrant_url,
            api_key=qdrant_api_key or None,
            timeout=qdrant_timeout_seconds,
            prefer_grpc=qdrant_prefer_grpc,
        )
        self._ensure_collection(embedding_dimensions)

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
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            )
        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )
        return point_ids

    def search(
        self,
        *,
        query: str,
        source_types: Iterable[str] | None = None,
        filters: dict[str, str | int | bool] | None = None,
        k: int = 4,
        min_similarity: float | None = None,
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

        response = self.client.query_points(
            collection_name=self.collection_name,
            query=self.embeddings.embed_query(query),
            query_filter=Filter(must=conditions),
            limit=max(1, k),
            score_threshold=min_similarity,
            with_payload=True,
            with_vectors=False,
        )
        matches = []
        for point in response.points:
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
        if self.client.collection_exists(self.collection_name):
            collection = self.client.get_collection(self.collection_name)
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
        else:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )

        for field_name in ("source_type", "intent", "severity", "cwe_id"):
            try:
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            except Exception:
                LOGGER.debug(
                    "Payload index already exists or cannot be created: %s",
                    field_name,
                    exc_info=True,
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
    )


def _build_embeddings(
    *,
    provider: str,
    model: str,
    cache_dir: str | Path,
    api_key: str,
    base_url: str | None,
) -> Embeddings:
    if provider == "fastembed":
        return FastEmbedEmbeddings(
            model_name=model,
            cache_dir=str(cache_dir),
        )
    if provider in {"openai", "tei"}:
        options = {
            "api_key": api_key,
            "model": model,
        }
        if base_url:
            options["base_url"] = base_url
        if provider == "tei":
            options["check_embedding_ctx_length"] = False
        return OpenAIEmbeddings(**options)
    raise ValueError(f"Unsupported embedding provider: {provider}")


def _point_id(source_type: str, source_id: str, chunk_index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"{source_type}:{source_id}:{chunk_index}"))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_source_type(source_type: str) -> None:
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"Unsupported knowledge source type: {source_type}")
