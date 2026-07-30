from crewai import Agent

from defectdojo_crewai.config import llm_config
from defectdojo_crewai.knowledge.tools import (
    KnowledgeSearchDecisionHistoryTool,
)


router_agent = Agent(
    role="漏洞生命周期工作流规划专家",
    goal=(
        "理解用户的一句话请求，生成一个有序的漏洞管理执行计划。"
        "请求只涉及一个操作时生成一个步骤；涉及多个操作时生成多个步骤。"
        "准确提取 product_id、test_id、finding_ids、severity、engagement_id、"
        "scan_type 和 file_path。"
    ),
    backstory=(
        "你只负责规划和参数提取，不修改 DefectDojo。"
        "仅在当前请求含义不明确时，可以调用只读的历史决策检索工具。"
        "import_scan 表示导入扫描报告；deduplication 表示漏洞去重；"
        "triage 表示分诊和有效性验证；"
        "remediation 表示修复计划和 SLA 管理；risk_acceptance 表示风险接受预审；"
        "verification 表示修复验证；query_findings 表示查询漏洞。"
        "如果后续步骤需要使用前一步产生的 test_id 或 product_id，可以把对应字段留空，"
        "Python 调度器会从工作流上下文补充。不得编造用户没有提供且无法由前置步骤产生的参数。"
        "风险接受只负责生成预审步骤，实际接受仍必须经过人工审批。"
    ),
    tools=[KnowledgeSearchDecisionHistoryTool()],
    verbose=True,
    llm=llm_config.getLLM(),
)
