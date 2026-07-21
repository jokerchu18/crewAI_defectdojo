from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from defectdojo_crewai.crews import rag_retriever


QDRANT_URL = "http://localhost:6333"


class DeterministicEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    @staticmethod
    def _embed(text: str) -> list[float]:
        lowered = text.lower()
        return [
            float(lowered.count("漏洞") + lowered.count("vulnerability")),
            float(lowered.count("sla") + lowered.count("期限")),
            float(lowered.count("风险") + lowered.count("risk")),
            0.1,
        ]


def main() -> None:
    collection_name = f"defectdojo_smoke_{uuid4().hex}"
    client = QdrantClient(url=QDRANT_URL)

    try:
        with TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir)
            (knowledge_dir / "sla.md").write_text(
                "高危漏洞的 SLA 修复期限为 30 天。",
                encoding="utf-8",
            )
            (knowledge_dir / "risk.md").write_text(
                "风险接受必须经过人工审批。",
                encoding="utf-8",
            )

            with patch.object(
                rag_retriever,
                "OpenAIEmbeddings",
                return_value=DeterministicEmbeddings(),
            ):
                vectorstore = _build(
                    knowledge_dir,
                    collection_name,
                    fingerprint="fingerprint-v1",
                )
                matches = rag_retriever.search_knowledge(
                    vectorstore,
                    query="漏洞 SLA 修复期限",
                    k=1,
                )
                assert matches
                assert "30 天" in matches[0]["content"]
                assert (
                    matches[0]["metadata"]["knowledge_fingerprint"]
                    == "fingerprint-v1"
                )

                with patch.object(
                    QdrantVectorStore,
                    "from_texts",
                    side_effect=AssertionError(
                        "Matching fingerprint should reuse the collection."
                    ),
                ):
                    reused = _build(
                        knowledge_dir,
                        collection_name,
                        fingerprint="fingerprint-v1",
                    )
                    reused_matches = rag_retriever.search_knowledge(
                        reused,
                        query="人工审批风险接受",
                        k=1,
                    )
                    assert "人工审批" in reused_matches[0]["content"]

                rebuilt = _build(
                    knowledge_dir,
                    collection_name,
                    fingerprint="fingerprint-v2",
                )
                rebuilt_matches = rag_retriever.search_knowledge(
                    rebuilt,
                    query="漏洞 SLA 修复期限",
                    k=1,
                )
                assert (
                    rebuilt_matches[0]["metadata"]["knowledge_fingerprint"]
                    == "fingerprint-v2"
                )

        count = client.count(
            collection_name=collection_name,
            exact=True,
        ).count
        assert count == 2
        print(
            "Qdrant smoke test passed: create, search, reuse, and rebuild."
        )
    finally:
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)
        client.close()


def _build(
    knowledge_dir: Path,
    collection_name: str,
    fingerprint: str,
):
    return rag_retriever.build_vectorstore(
        doc_dir=knowledge_dir,
        embedding_provider="openai",
        api_key="test-key",
        base_url=None,
        embedding_model="test-embedding",
        embedding_cache_dir=knowledge_dir / "models",
        qdrant_url=QDRANT_URL,
        qdrant_api_key=None,
        qdrant_collection_name=collection_name,
        qdrant_timeout_seconds=10,
        qdrant_prefer_grpc=False,
        knowledge_fingerprint=fingerprint,
    )


if __name__ == "__main__":
    main()
