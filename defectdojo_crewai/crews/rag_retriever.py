from pathlib import Path

from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient


def build_vectorstore(
    doc_dir: str | Path,
    embedding_provider: str,
    api_key: str,
    base_url: str | None,
    embedding_model: str,
    embedding_cache_dir: str | Path,
    qdrant_url: str,
    qdrant_api_key: str | None,
    qdrant_collection_name: str,
    qdrant_timeout_seconds: int,
    qdrant_prefer_grpc: bool,
    knowledge_fingerprint: str,
):
    documents: list[dict[str, str]] = []
    base_path = Path(doc_dir)

    if not base_path.is_dir():
        raise FileNotFoundError(f"Knowledge base directory does not exist: {base_path}")

    for file_path in base_path.rglob("*.md"):
        text = file_path.read_text(encoding="utf-8")
        if not text.strip():
            continue
        documents.append({
            "source": str(file_path),
            "text": text,
        })

    if not documents:
        raise ValueError(f"No non-empty Markdown files found in: {base_path}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
    )

    chunks: list[str] = []
    metadatas: list[dict[str, str]] = []

    for doc in documents:
        split_texts = splitter.split_text(doc["text"])
        for chunk in split_texts:
            chunks.append(chunk)
            metadatas.append({
                "source": doc["source"],
                "knowledge_fingerprint": knowledge_fingerprint,
            })

    embeddings = _build_embeddings(
        provider=embedding_provider,
        model=embedding_model,
        cache_dir=embedding_cache_dir,
        api_key=api_key,
        base_url=base_url,
    )

    client_options = {
        "url": qdrant_url,
        "api_key": qdrant_api_key or None,
        "timeout": qdrant_timeout_seconds,
        "prefer_grpc": qdrant_prefer_grpc,
    }
    client = QdrantClient(**client_options)
    if client.collection_exists(qdrant_collection_name):
        stored_fingerprint = _collection_fingerprint(
            client,
            qdrant_collection_name,
        )
        if stored_fingerprint == knowledge_fingerprint:
            return QdrantVectorStore(
                client=client,
                collection_name=qdrant_collection_name,
                embedding=embeddings,
                validate_collection_config=False,
            )
    client.close()

    return QdrantVectorStore.from_texts(
        texts=chunks,
        embedding=embeddings,
        metadatas=metadatas,
        collection_name=qdrant_collection_name,
        force_recreate=True,
        **client_options,
    )


def _build_embeddings(
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
    if provider == "openai":
        embedding_options = {
            "api_key": api_key,
            "model": model,
        }
        if base_url:
            embedding_options["base_url"] = base_url
        return OpenAIEmbeddings(**embedding_options)
    raise ValueError(f"Unsupported embedding provider: {provider}")


def _collection_fingerprint(
    client: QdrantClient,
    collection_name: str,
) -> str | None:
    points, _ = client.scroll(
        collection_name=collection_name,
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if not points or not points[0].payload:
        return None
    metadata = points[0].payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    fingerprint = metadata.get("knowledge_fingerprint")
    return str(fingerprint) if fingerprint else None


def search_knowledge(vectorstore, query: str, k: int = 4) -> list[dict]:
    docs = vectorstore.similarity_search(query, k=max(1, k))
    return [
        {
            "content": doc.page_content,
            "metadata": doc.metadata,
        }
        for doc in docs
    ]
