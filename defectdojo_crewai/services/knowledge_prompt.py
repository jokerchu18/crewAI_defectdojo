import hashlib
import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.crews.rag_retriever import (
    build_vectorstore,
    search_knowledge,
)


_LOGGER = logging.getLogger(__name__)
_VECTORSTORE = None
_KNOWLEDGE_FINGERPRINT: tuple[tuple[str, int, int], ...] | None = None
_VECTORSTORE_LOCK = Lock()


def prepare_task_with_knowledge(task, query: str):
    """Return an isolated task whose prompt includes retrieved knowledge."""
    prepared_task = task.model_copy()
    knowledge_context = load_knowledge_context(query)
    knowledge_context = knowledge_context.replace("{", "{{").replace("}", "}}")
    prepared_task.description = (
        f"{prepared_task.description}\n\n{knowledge_context}"
    )
    return prepared_task


def load_knowledge_context(query: str) -> str:
    header = (
        "【企业知识库上下文】\n"
        "以下内容由应用在本次执行前检索并直接加载到 prompt 中，"
        "不是工具调用结果。请将其作为业务规则和案例参考；"
        "若与任务的明确约束冲突，以任务约束为准。\n"
    )
    if not settings.knowledge_enabled:
        return header + "知识库检索已禁用。"
    if not query.strip():
        return header + "本次请求没有可用的检索关键词。"

    try:
        vectorstore = _get_vectorstore()
        matches = search_knowledge(
            vectorstore,
            query=query,
            k=settings.knowledge_top_k,
        )
    except (FileNotFoundError, ValueError) as exc:
        _LOGGER.info("Knowledge context is unavailable: %s", exc)
        return header + "本次没有可用的知识库内容。"
    except Exception:
        _LOGGER.exception("Failed to retrieve knowledge context")
        return header + "知识库检索失败，本次任务仅依据原始 prompt 执行。"

    sections: list[str] = []
    used_chars = 0
    for index, match in enumerate(matches, start=1):
        content = str(match.get("content") or "").strip()
        if not content:
            continue
        source = _display_source(match.get("metadata") or {})
        section = f"[知识片段 {index} | 来源: {source}]\n{content}"
        remaining = settings.knowledge_max_chars - used_chars
        if remaining <= 0:
            break
        if len(section) > remaining:
            section = section[:remaining].rstrip()
        sections.append(section)
        used_chars += len(section)

    if not sections:
        return header + "没有检索到与本次任务相关的知识片段。"
    return header + "\n\n".join(sections)


def _get_vectorstore():
    global _VECTORSTORE, _KNOWLEDGE_FINGERPRINT

    fingerprint = _knowledge_fingerprint(settings.knowledge_base_dir)
    with _VECTORSTORE_LOCK:
        if _VECTORSTORE is None or fingerprint != _KNOWLEDGE_FINGERPRINT:
            _VECTORSTORE = build_vectorstore(
                doc_dir=settings.knowledge_base_dir,
                embedding_provider=settings.embedding_provider,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                embedding_model=settings.embedding_model,
                embedding_cache_dir=settings.embedding_cache_dir,
                qdrant_url=settings.qdrant_url,
                qdrant_api_key=settings.qdrant_api_key,
                qdrant_collection_name=settings.qdrant_collection_name,
                qdrant_timeout_seconds=settings.qdrant_timeout_seconds,
                qdrant_prefer_grpc=settings.qdrant_prefer_grpc,
                knowledge_fingerprint=_index_fingerprint(fingerprint),
            )
            _KNOWLEDGE_FINGERPRINT = fingerprint
    return _VECTORSTORE


def _knowledge_fingerprint(
    doc_dir: Path,
) -> tuple[tuple[str, int, int], ...]:
    if not doc_dir.is_dir():
        raise FileNotFoundError(
            f"Knowledge base directory does not exist: {doc_dir}"
        )
    return tuple(
        (
            str(path.relative_to(doc_dir)),
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in sorted(doc_dir.rglob("*.md"))
        if path.is_file()
    )


def _index_fingerprint(
    fingerprint: tuple[tuple[str, int, int], ...],
) -> str:
    index_state = {
        "documents": fingerprint,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
    }
    serialized = json.dumps(
        index_state,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _display_source(metadata: dict[str, Any]) -> str:
    source = metadata.get("source")
    if not source:
        return "unknown"
    source_path = Path(str(source))
    try:
        return str(source_path.relative_to(settings.knowledge_base_dir))
    except ValueError:
        return source_path.name
