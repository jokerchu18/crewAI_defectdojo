from crewai import Task

from defectdojo_crewai.agents.risk_acceptance import risk_acceptance_agent
from defectdojo_crewai.tasks.remediation_tasks import remediation_task

risk_acceptance_review_task = Task(
    description=(
        "评估每个 finding 是否应进行风险接受。\n"
        "规则如下：\n"
        "1. 对每个 finding，在做 Accept/Reject 判断之前，"
        "必须先从 vulnerability_ids、cwe、title、description 中提取 CVE/CWE/OWASP 标识；"
        "如果存在任一标识，必须调用 knowledge_graph_lookup 获取 KEV、EPSS、"
        "CVSS 严重性和 CWE 根因证据。"
        "没有标识或无匹配时，必须在 reason 中记录“KG 无匹配证据”。\n"
        "2. 只允许 severity 为 Medium、Low、Info 的 finding 输出 Accept；"
        "若 KG 证据显示该漏洞在 KEV 列表中或 EPSS 较高，应倾向 Reject。\n"
        "3. High 和 Critical 必须输出 Reject。\n"
        "4. 对每个 finding 都必须输出固定字段："
        "finding_id, severity, title, decision, reason, "
        "expiration_date, reactivate_expired, restart_sla_expired。\n"
        "5. decision 只能是 Accept 或 Reject。\n"
        "6. 只有 decision=Accept 的 finding 才会进入人工审批；"
        "decision=Reject 的 finding 仅作为评估结果输出，不进入审批。"
        "本任务只生成预审建议，创建与更新操作由审批层执行。\n"
        "最终只输出合法 JSON，不要使用 Markdown。根字段必须是 candidates，"
        "其值是候选项数组。"
    ),
    expected_output="结构化风险接受预审结果",
    agent=risk_acceptance_agent,
)

risk_acceptance_request_task = Task(
    description=(
        "用户请求评估 Test ID {test_id} 下的漏洞是否适合风险接受。\n"
        "严重级别过滤条件为 {severity_filter}。\n"
        "1. 必须先调用 defectdojo_get_finding_by_test_tool 获取真实 findings。\n"
        "2. 只分析 active=True 且符合过滤条件的 finding。\n"
        "3. 对每个 finding，在做 Accept/Reject 判断之前，"
        "必须先从 vulnerability_ids、cwe、title、description 中提取 CVE/CWE/OWASP 标识；"
        "如果存在任一标识，必须调用 knowledge_graph_lookup 获取 KEV、EPSS、"
        "CVSS 严重性和 CWE 根因证据。"
        "不得根据用户消息猜测或编造 ID。"
        "如果没有标识或 KG 返回 no_match，必须在 reason 中记录“KG 无匹配证据”。"
        "KG 结果只能作为证据，不能覆盖 finding 原始字段。\n"
        "4. Critical 和 High 必须输出 Reject。\n"
        "5. Medium、Low、Info 可以结合实际风险判断 Accept 或 Reject；"
        "若 KG 证据显示该漏洞在 KEV 列表中或 EPSS 较高，应倾向 Reject 并在 reason 中说明。\n"
        "6. decision=Accept 时必须给出 expiration_date，格式为 YYYY-MM-DD。\n"
        "7. 必须明确 reactivate_expired 和 restart_sla_expired。\n"
        "8. 本任务只能生成预审建议，不能创建 Risk Acceptance 或更新 Finding；"
        "写操作在人工审批通过后由审批层统一执行。\n"
        "9. 返回字段必须包含 finding_id、severity、title、decision、reason、"
        "expiration_date、reactivate_expired、restart_sla_expired，"
        "其中 reason 需包含 KG 证据结论或“KG 无匹配证据”。\n"
        "最终只输出合法 JSON，不要使用 Markdown。根字段必须是 candidates。"
    ),
    expected_output="结构化风险接受预审结果",
    agent=risk_acceptance_agent,
)
