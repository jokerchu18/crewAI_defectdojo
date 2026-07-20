from crewai import Task

from defectdojo_crewai.agents.router import router_agent
router_task = Task(
    description=(
        "分析以下用户请求：\n"
        "{user_message}\n\n"
        "把请求拆成一个或多个有序步骤。每个步骤的 intent 只能是 "
        "risk_acceptance、deduplication、triage、remediation、verification、import_scan、"
        "query_findings、unknown 之一。\n"
        "常见工作流示例：\n"
        "- “导入报告并分诊” => import_scan，然后 triage。\n"
        "- “导入报告、去重并分诊” => import_scan、deduplication、triage。\n"
        "- “导入、分诊并制定修复计划” => import_scan、triage、remediation。\n"
        "- “查询 Product 1 并评估风险接受” => query_findings、risk_acceptance。\n"
        "使用 depends_on 表示直接依赖的 step_id。后续步骤需要前一步产生的 ID 时，"
        "不要猜测 ID，保留 null 即可。\n"
        "risk_acceptance 必须放在 steps 的最后，因为该步骤可能暂停等待人工审批。\n"
        "单 Agent 请求也必须输出 steps 数组，但数组中只有一个步骤。\n"
        "最终只输出合法 JSON，不要 Markdown。格式必须是："
        "{\"steps\":[{\"step_id\":\"step_1\",\"intent\":\"...\","
        "\"product_id\":null,\"test_id\":null,\"finding_ids\":[],"
        "\"severity\":null,\"engagement_id\":null,\"scan_type\":null,"
        "\"file_path\":null,\"depends_on\":[],\"instruction\":\"...\"}],"
        "\"message\":\"\"}。"
    ),
    expected_output="包含有序 steps 数组的合法 JSON 工作流计划",
    agent=router_agent,
)
