"""Integration smoke test for four logical Qdrant knowledge partitions."""

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from qdrant_client import QdrantClient

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.knowledge.storage import (
    SOURCE_AUDIT,
    SOURCE_LIBRARY,
    SOURCE_REMEDIATION,
    SOURCE_TRIAGE,
    build_knowledge_store,
)


def main() -> None:
    collection_name = f"defectdojo_knowledge_smoke_{uuid4().hex}"
    store = build_knowledge_store(
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
        embedding_cache_dir=settings.embedding_cache_dir,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        qdrant_url=settings.qdrant_url,
        qdrant_api_key=settings.qdrant_api_key,
        qdrant_collection_name=collection_name,
        qdrant_timeout_seconds=settings.qdrant_timeout_seconds,
        qdrant_prefer_grpc=settings.qdrant_prefer_grpc,
    )
    client = QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    try:
        with TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            (knowledge_dir / "library.md").write_text(
                "CWE-79 跨站脚本漏洞需要进行输出编码和上下文转义。",
                encoding="utf-8",
            )
            assert store.sync_markdown_library(knowledge_dir) == 1
            store.upsert_texts(
                texts=["用户请求导入扫描报告，最终成功完成导入工作流。"],
                source_type=SOURCE_AUDIT,
                source_id="workflow-1",
                metadata=[{"intent": "import_scan", "outcome": "completed"}],
            )
            store.upsert_texts(
                texts=["已批准的误报分诊：CWE-79 在不可达测试端点上关闭。"],
                source_type=SOURCE_TRIAGE,
                source_id="approval-1",
                metadata=[{"severity": "Low", "cwe_id": "79"}],
            )
            store.upsert_texts(
                texts=["已验证修复：升级组件并关闭受影响接口后漏洞已缓解。"],
                source_type=SOURCE_REMEDIATION,
                source_id="approval-2",
                metadata=[{"severity": "High", "cwe_id": "79"}],
            )

            assert store.search(
                query="如何处理 CWE-79？",
                source_types=[SOURCE_LIBRARY],
                k=1,
            )[0]["metadata"]["source_type"] == SOURCE_LIBRARY
            assert store.search(
                query="历史导入请求",
                source_types=[SOURCE_AUDIT],
                filters={"intent": "import_scan"},
                k=1,
            )[0]["metadata"]["source_type"] == SOURCE_AUDIT
            assert store.search(
                query="误报分诊",
                source_types=[SOURCE_TRIAGE],
                k=1,
            )[0]["metadata"]["source_type"] == SOURCE_TRIAGE
            assert store.search(
                query="已验证修复",
                source_types=[SOURCE_REMEDIATION],
                k=1,
            )[0]["metadata"]["source_type"] == SOURCE_REMEDIATION

        count = client.count(collection_name=collection_name, exact=True).count
        assert count == 4
        print("Qdrant four-partition smoke test passed.")
    finally:
        store.close()
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)
        client.close()


if __name__ == "__main__":
    main()
