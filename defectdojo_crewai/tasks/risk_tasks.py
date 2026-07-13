from crewai import Task

from defectdojo_crewai.agents.risk_acceptance import risk_acceptance_agent, risk_acceptance_review_agent, risk_acceptance_execute_agent
from defectdojo_crewai.models.schemas import RiskAcceptanceReviewResult
from defectdojo_crewai.tasks.remediation_tasks import remediation_task

risk_acceptance_review_task = Task(
    description=(
        "基于 remediation_task 的输出，评估每个 finding 是否应进行风险接受。\n"
        "规则如下：\n"
        "1. 只允许 severity 为 Medium、Low、Info 的 finding 输出 Accept。\n"
        "2. High 和 Critical 必须输出 Reject。\n"
        "3. 对每个 finding 都必须输出固定字段："
        "finding_id, severity, title, decision, reason, "
        "expiration_date, reactivate_expired, restart_sla_expired。\n"
        "4. decision 只能是 Accept 或 Reject。\n"
        "5. 只有 decision=Accept 的 finding 才会进入人工审批；"
        "decision=Reject 的 finding 仅作为评估结果输出，不进入审批。"
    ),
    expected_output="结构化风险接受预审结果",
    agent=risk_acceptance_review_agent,
    context=[remediation_task],
    output_pydantic=RiskAcceptanceReviewResult,
)

risk_acceptance_execute_task = Task(
    description=(
        "你将接收一个已经人工审批通过的 approved_candidates 列表。\n"
        "输入包含：human_approved 和 approved_candidates。\n"
        "只有当 human_approved=True 时，才允许执行。\n"
        "请遍历 approved_candidates 中的每个元素，每个元素包含："
        "finding_id, severity, title, decision, reason, expiration_date, "
        "reactivate_expired, restart_sla_expired。\n"
        "对每个元素执行以下步骤：\n"
        "1. 如果 decision 不是 Accept，则跳过。\n"
        "2. 调用 DefectDojoCreateRiskAcceptanceTool 创建风险接受记录。\n"
        "3. 必须完整传入 accepted_findings、expiration_date、"
        "reactivate_expired、restart_sla_expired。\n"
        "4. 创建成功后，调用 DefectDojoUpdateFindingTool 更新 finding："
        "risk_accepted=True, active=False。\n"
        "5. 输出每个 finding 的执行结果。\n"
        "如果 human_approved 不为 True，则拒绝执行。"
    ),
    expected_output="批量风险接受执行结果",
    agent=risk_acceptance_execute_agent,
)

risk_acceptance_task = Task(
    description=(
        "你将接收 remediation_task 的输出结果，并对其中的 findings 执行风险接受决策。\n"
        "请严格按照以下流程执行：\n"
        "1. 接收风险接受请求，输入来自 remediation_task 的输出。\n"
        "2. 对每个 finding 评估安全建议，只能在以下建议中选择一种："
        "Accept / Avoid / Mitigate / Fix / Transfer。\n"
        "3. 决策规则：\n"
        "   - 如果 finding.severity 是 Critical 或 High，则不要接受风险，优先输出 Fix 或 Mitigate。\n"
        "   - 如果 finding.severity 是 Medium、Low、Info，则结合漏洞描述、可利用性、业务影响、"
        "修复成本、SLA 压力等因素，自主判断是否 Accept。\n"
        "   - 只有明确判断为 Accept 的 finding，才允许创建 Risk Acceptance。\n"
        "4. 如果决定 Accept，则必须调用 DefectDojoCreateRiskAcceptanceTool 创建风险接受记录，"
        "并且下面四个参数必须完整提供且符合规则：\n"
        "   - accepted_findings.set(findings): 必须包含当前被接受的 finding id\n"
        "   - expiration_date: 必须明确填写到期时间，建议根据风险水平设置未来 N 天\n"
        "   - reactivate_expired: 必须明确为 True 或 False\n"
        "   - restart_sla_expired: 必须明确为 True 或 False\n"
        "5. 创建 Risk Acceptance 成功后，必须调用 DefectDojoUpdateFindingTool 更新对应 Finding，至少更新：\n"
        "   - risk_accepted=True\n"
        "   - active=False\n"
        "6. 对于不接受风险的 finding，不要调用创建 Risk Acceptance 的工具，只输出你的处理建议及理由。\n"
        "7. 输出时必须逐个 finding 给出：finding_id、severity、decision、reason、"
        "是否创建 risk acceptance、是否更新 finding。"
    ),
    expected_output=(
        "一个结构化风险接受决策结果。对每个 finding 都要包含："
        "finding_id、severity、decision(Accept/Avoid/Mitigate/Fix/Transfer)、reason、"
        "risk_acceptance_created(True/False)、risk_acceptance_payload、"
        "finding_updated(True/False)、finding_update_payload。"
    ),
    agent=risk_acceptance_agent,
    context=[remediation_task],
)