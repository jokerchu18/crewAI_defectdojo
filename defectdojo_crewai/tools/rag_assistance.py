from pydantic import BaseModel, Field
from crewai.tools import BaseTool

from defectdojo_crewai.config import settings
from defectdojo_crewai.crews.rag_retriever import build_vectorstore, search_knowledge


class KnowledgeSearchInput(BaseModel):
    query: str = Field(..., description="Knowledge search query")


class KnowledgeSearchTool(BaseTool):
    name: str = "knowledge_search_tool"
    description: str = "检索企业安全知识库，返回与当前决策相关的规则和案例"
    args_schema: type[BaseModel] = KnowledgeSearchInput

    _vectorstore = None

    def _run(self, query: str):
        if self._vectorstore is None:
            self._vectorstore = build_vectorstore(
                doc_dir=settings.knowledge_base_dir,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                embedding_model=settings.embedding_model,
            )

        return search_knowledge(self._vectorstore, query=query, k=4)