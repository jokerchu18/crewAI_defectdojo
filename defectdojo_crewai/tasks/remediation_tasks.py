from crewai import Task
from defectdojo_crewai.agents.remediation import remediation_agent
from defectdojo_crewai.tasks.import_tasks import import_scan_task

remediation_task = Task(
    description=(
        "获取 Product ID 的所有活跃漏洞，并执行修复跟踪分析。"
        "\n"
        "请严格按以下步骤执行："
        "\n"
        "1. 调用 DefectDojoGetFindingByProductIDTool，获取该 Product ID 下所有 active=True 的 findings，注意返回值中的id 字段就是 finding ID"
        "\n"
        "2. 对每个 finding 计算 SLA 信息："
        "- 严重级别对应的 SLA 总天数：Critical=7, High=30, Medium=90, Low=180, Info=365；"
        "- SLA 起始日期优先取 sla_start_date，若为空则取 date；"
        "- 如果返回中已有 sla_expiration_date 和 sla_days_remaining，可直接使用；"
        "- 如果缺失，则根据起始日期和严重级别天数自行推导。"
        "\n"
        "3. 对每个 finding 计算优先级分数："
        "- 优先级 = 严重级别权重 × 资产重要性 × 利用概率 × 剩余时间因子"
        "- 严重级别权重：Critical=5, High=4, Medium=3, Low=2, Info=1"
        "- 资产重要性：从 Product 的业务重要性获取；若未提供，则使用默认值并在输出中说明"
        "- 利用概率：优先使用 epss_score；若无 epss_score，但 known_exploited=True 或存在 KEV 证据，则提高利用概率判断"
        "- 剩余时间因子：根据 SLA 剩余天数和 SLA 总天数计算，剩余时间越少，优先级越高"
        "\n"
        "4. 按优先级从高到低排序，标记已超期和即将到期漏洞。"
        "\n"
        "5. 为每个 finding 生成修复建议，并且你必须对每个 finding 调用 DefectDojoUpdateFindingTool，只传入本阶段需要更新的字段,"
        "不要重复传入无关字段。至少更新以下字段："
        "- planned_remediation_date"
        "- effort_for_fixing"
        "- under_review"
        "\n"
        "6. 字段更新要求如下："
        "- planned_remediation_date：根据 severity、SLA 剩余时间和优先级，给出明确计划修复日期，注意时间一律参考北京时间"
        "- effort_for_fixing：给出修复工作量建议，例如 low / medium / high，或简短文本"
        "- under_review：设为 True，表示该漏洞已进入修复跟踪流程"
        "\n"
        "7. 如果你有足够信息，也可以额外更新以下字段："
        "- planned_remediation_version"
        "- reviewers"
        "\n"
        "8. 不要只做分析而不执行更新。每个 finding 都必须至少执行一次 DefectDojoUpdateFindingTool。"
        "\n"
        "9. 最终输出必须包含每个 finding 的："
        "- finding_id"
        "- title"
        "- severity"
        "- sla_days_remaining"
        "- priority_score"
        "- overdue_status"
        "- remediation_recommendation"
        "- update_executed"
        "- updated_fields"
        "- updated_values"
    ),
    expected_output=(
        "按优先级排序的修复跟踪结果列表。每个漏洞都包含SLA状态、优先级、修复建议，"
        "并明确展示调用 update tool 更新了哪些字段以及对应值。"
    ),
    agent=remediation_agent,
    context=[import_scan_task]
)