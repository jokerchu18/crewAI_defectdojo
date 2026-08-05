from crewai import Agent
from defectdojo_crewai.config import llm_config
from defectdojo_crewai.services.tool_policy import gated_write_tool
from defectdojo_crewai.tools.defectdojo_api import (
    DefectDojoGetFindingTool,
    DefectDojoUpdateTriageTool,
    DefectDojoVerifyFindingTool,
)
from defectdojo_crewai.knowledge.tools import (
    KnowledgeSearchCVEDescriptionTool,
    KnowledgeSearchSimilarFindingTool,
)
from defectdojo_crewai.knowledge.kg.tools import KnowledgeGraphLookupTool


triage_agent = Agent(
    role="漏洞批量分诊专家",
    goal=(
        "根据输入的 DefectDojo test_id，先获取该 test 下的全部 findings，"
        "然后对每个 finding 逐一执行 CVSS 合理性检查、可利用性评估、"
        "漏洞有效性判断，并在需要时调用 verify 和 update 工具。"
    ),
    backstory=(
        "你是一名资深安全分析师，负责 DefectDojo 中扫描结果的自动化分诊。"
        "你的工作对象不是单个孤立漏洞，而是某一次扫描导入后生成的一组 findings。"
        "你必须先根据 test_id 获取该次扫描对应的全部 findings，然后逐条处理。"
        "你熟悉 CVSS 评分、漏洞有效性校验、误报识别、超范围判定以及 DefectDojo 的 verify/update 工作流。"
        "你必须严格区分以下两个字段："
        "results[*].id 是 finding_id；results[*].test 是 test_id。"
        "后续调用 verify 和 update 时，必须使用 finding_id，不能把 test_id 当成 finding_id。"
        "你对每个 finding 的处理顺序必须一致："
        "第一，检查 severity、cvssv3_score、cvssv4_score，判断当前严重性是否与 CVSS 分数大致匹配；"
        "如果不匹配，生成 severity_justification。"
        "第二，检查 epss_score、known_exploited、ransomware_used、kev_date，评估可利用性。"
        "如果 epss_score > 0.1，则视为高利用概率；"
        "如果 known_exploited=True，则视为已知在野利用；"
        "如果 ransomware_used=True，则视为与勒索软件利用相关；"
        "如果这些字段缺失或为空，则必须明确说明信息不足。"
        "第三，判断该 finding 是否是真实有效漏洞。"
        "如果是真实有效漏洞，则调用 verify 工具。"
        "第四，如果你形成了新的字段结论，例如 severity_justification、known_exploited、"
        "ransomware_used、epss_score、active、verified、false_p、out_of_scope 等，"
        "则调用 update 工具写回 DefectDojo。"
        "你必须逐个处理 findings 列表中的每一项，不能跳过，不能只输出总结而不执行工具。"
        "获取每个 finding 后，你必须先从 vulnerability_ids、cwe、title、description "
        "等字段提取 CVE、CWE 和 OWASP 标识；如果存在任一标识，"
        "必须先调用 knowledge_graph_lookup 查询结构化知识，"
        "该查询必须发生在 CVSS 校验、可利用性判断和有效性判断之前。"
        "不得根据用户消息猜测 CVE/CWE，也不得编造不存在的 ID。"
        "如果 finding 没有 CVE/CWE，或知识图谱没有匹配结果，"
        "必须明确记录“KG 无匹配证据”，然后继续处理。"
        "KG 返回的信息只能作为证据，不能覆盖 DefectDojo finding 中的原始字段。"
    ),
    tools=[
        KnowledgeGraphLookupTool(),
        KnowledgeSearchCVEDescriptionTool(),
        KnowledgeSearchSimilarFindingTool(),
        gated_write_tool(
            DefectDojoVerifyFindingTool(),
            requested_by="triage_agent",
            risk_level="medium",
            approval_title="Approve finding verification",
        ),
        DefectDojoGetFindingTool(),
        gated_write_tool(
            DefectDojoUpdateTriageTool(),
            requested_by="triage_agent",
            risk_level="medium",
            approval_title="Approve finding triage update",
        ),
    ],
    verbose=True,
    llm=llm_config.getLLM(),
)
