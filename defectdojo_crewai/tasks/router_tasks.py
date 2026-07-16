from crewai import Task

from defectdojo_crewai.agents.router import router_agent
router_task = Task(
    description=(
        "分析以下用户请求：\n"
        "{user_message}\n\n"
        "识别 intent，并提取 product_id、test_id、finding_ids、severity、"
        "engagement_id、scan_type、file_path。\n"
        "intent 只能是 risk_acceptance、triage、remediation、verification、"
        "import_scan、query_findings、unknown 之一。\n"
        "各意图的必要参数如下：remediation 和 risk_acceptance 只要求 product_id；"
        "triage 要求 test_id；query_findings 要求 product_id 或 test_id；"
        "import_scan 可以使用系统默认配置；verification 要求 finding_ids。"
        "不要把非必要字段描述为缺失。不得猜测用户没有提供的 ID；"
        "只有缺少该意图的必要参数时才在 message 中说明。\n"
        "最终只输出一个合法 JSON 对象，不要使用 Markdown 代码块。"
        "JSON 必须包含 intent、product_id、test_id、finding_ids、severity、"
        "engagement_id、scan_type、file_path、message；"
        "未知的单值字段使用 null，未知的 finding_ids 使用空数组。"
    ),
    expected_output="包含全部意图字段的合法 JSON 对象",
    agent=router_agent,
)
