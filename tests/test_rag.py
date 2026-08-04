"""Manual checks for tool-driven knowledge retrieval."""

from defectdojo_crewai.agents.triage import triage_agent
from defectdojo_crewai.knowledge.tools import (
    KnowledgeSearchCVEDescriptionTool,
)


TRIAGE_QUERY = (
    "漏洞分诊时不能只看 CVSS 分数，"
    "如何结合已知在野利用、互联网暴露和资产重要性判断处置优先级？"
)
EXPECTED_PHRASES = [
    "CVSS",
    "已知在野利用",
]


def test_library_tool_retrieves_triage_knowledge() -> None:
    """The library tool returns Markdown knowledge without prompt injection."""
    matches = KnowledgeSearchCVEDescriptionTool().run(
        query=TRIAGE_QUERY,
        top_k=4,
        min_similarity=0,
    )
    assert matches, "知识库工具没有检索到任何 library 记录。"
    content = "\n".join(match["content"] for match in matches)
    print(content)
    missing = [phrase for phrase in EXPECTED_PHRASES if phrase not in content]
    assert not missing, f"检索结果缺少分诊关键短语: {missing}"


def test_triage_agent_has_read_only_knowledge_tools() -> None:
    tool_names = {tool.name for tool in triage_agent.tools}
    assert "knowledge_search_cve_description" in tool_names
    assert "knowledge_search_similar_finding" in tool_names


if __name__ == "__main__":
    test_library_tool_retrieves_triage_knowledge()
    test_triage_agent_has_read_only_knowledge_tools()
    print("Tool-driven RAG checks passed.")
