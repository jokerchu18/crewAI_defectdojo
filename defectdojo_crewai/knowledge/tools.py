"""Read-only CrewAI tools for the four knowledge partitions."""

from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.knowledge.storage import (
    SOURCE_AUDIT,
    SOURCE_LIBRARY,
    SOURCE_REMEDIATION,
    SOURCE_TRIAGE,
)
from defectdojo_crewai.knowledge.retrieval import search_knowledge


class KnowledgeSearchInput(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    filters: dict[str, str | int | bool] = Field(default_factory=dict)
    min_similarity: float | None = Field(default=None, ge=0, le=1)


def _search(
    *,
    query: str,
    source_type: str,
    top_k: int,
    filters: dict[str, str | int | bool],
    min_similarity: float | None,
) -> list[dict[str, Any]]:
    return search_knowledge(
        query=query,
        source_types=[source_type],
        filters=filters,
        k=top_k,
        min_similarity=(
            settings.knowledge_min_similarity
            if min_similarity is None
            else min_similarity
        ),
    )


class KnowledgeSearchDecisionHistoryTool(BaseTool):
    name: str = "knowledge_search_decision_history"
    description: str = "检索过去的 Router 工作流决策、置信度和实际 outcome。"
    args_schema: type[BaseModel] = KnowledgeSearchInput

    def _run(self, query: str, top_k: int = 5, filters: dict | None = None,
             min_similarity: float | None = None) -> list[dict[str, Any]]:
        return _search(query=query, source_type=SOURCE_AUDIT, top_k=top_k,
                       filters=filters or {}, min_similarity=min_similarity)


class KnowledgeSearchSimilarFindingTool(BaseTool):
    name: str = "knowledge_search_similar_finding"
    description: str = "检索已经人工批准并成功执行的相似漏洞分诊、误报和风险接受记录。"
    args_schema: type[BaseModel] = KnowledgeSearchInput

    def _run(self, query: str, top_k: int = 5, filters: dict | None = None,
             min_similarity: float | None = None) -> list[dict[str, Any]]:
        return _search(query=query, source_type=SOURCE_TRIAGE, top_k=top_k,
                       filters=filters or {}, min_similarity=min_similarity)


class KnowledgeSearchRemediationPatternTool(BaseTool):
    name: str = "knowledge_search_remediation_pattern"
    description: str = "检索已经完成缓解并记录成功结果的相似漏洞修复模式。"
    args_schema: type[BaseModel] = KnowledgeSearchInput

    def _run(self, query: str, top_k: int = 5, filters: dict | None = None,
             min_similarity: float | None = None) -> list[dict[str, Any]]:
        return _search(query=query, source_type=SOURCE_REMEDIATION, top_k=top_k,
                       filters=filters or {}, min_similarity=min_similarity)


class KnowledgeSearchCVEDescriptionTool(BaseTool):
    name: str = "knowledge_search_cve_description"
    description: str = "检索 library 分区中的 CWE、CVE、OWASP 和漏洞治理标准知识。"
    args_schema: type[BaseModel] = KnowledgeSearchInput

    def _run(self, query: str, top_k: int = 5, filters: dict | None = None,
             min_similarity: float | None = None) -> list[dict[str, Any]]:
        return _search(query=query, source_type=SOURCE_LIBRARY, top_k=top_k,
                       filters=filters or {}, min_similarity=min_similarity)
