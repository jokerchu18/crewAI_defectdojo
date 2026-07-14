from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings


def build_vectorstore(doc_dir: str, api_key: str, base_url: str, embedding_model: str):
    documents = []
    base_path = Path(doc_dir)

    for file_path in base_path.rglob("*.md"):
        text = file_path.read_text(encoding="utf-8")
        documents.append({
            "source": str(file_path),
            "text": text,
        })

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
    )

    chunks = []
    metadatas = []

    for doc in documents:
        split_texts = splitter.split_text(doc["text"])
        for chunk in split_texts:
            chunks.append(chunk)
        metadatas.append({"source": doc["source"]})

    embeddings = OpenAIEmbeddings(
        api_key=api_key,
        base_url=base_url,
        model=embedding_model,
    )

    vectorstore = FAISS.from_texts(
        texts=chunks,
        embedding=embeddings,
        metadatas=metadatas,
    )

    return vectorstore


def search_knowledge(vectorstore, query: str, k: int = 4) -> list[dict]:
    docs = vectorstore.similarity_search(query, k=k)
    return [
        {
            "content": doc.page_content,
            "metadata": doc.metadata,
        }
        for doc in docs
    ]