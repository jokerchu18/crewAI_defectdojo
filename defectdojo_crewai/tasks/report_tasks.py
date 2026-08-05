from crewai import Task

from defectdojo_crewai.agents.report import report_agent

report_task = Task(
    description=(
        "以下是本次漏洞管理工作流的执行信息，请据此撰写一份漏洞分析报告。\n"
        "用户原始请求：{user_message}\n"
        "工作流整体状态：{workflow_status}\n"
        "各步骤执行结果（JSON）：\n{step_results}\n"
        "\n"
        "撰写要求：\n"
        "1. 只能使用上述数据，不得编造漏洞、评分、CVE/CWE 编号或处置结果；"
        "缺失的信息标注“数据缺失”。\n"
        "2. 必须区分 DefectDojo 原始数据与知识图谱（KG）推导证据；"
        "无 KG 证据时如实写“KG 无匹配证据”。\n"
        "3. 报告为 Markdown 格式，参照漏洞分析报告结构，包含以下章节：\n"
        "   # 漏洞分析报告\n"
        "   ## 1. 概述\n"
        "   （本次任务背景、用户请求、涉及的 Product/Engagement/Test、工作流状态）\n"
        "   ## 2. 执行摘要\n"
        "   （各步骤一句话结论；漏洞总数与严重级别分布，能统计则统计，不能则说明）\n"
        "   ## 3. 漏洞详情分析\n"
        "   （逐条列出涉及的 finding：finding_id、标题、严重级别、CVSS/EPSS/KEV、"
        "CVE/CWE/OWASP 关联、KG 证据、有效性/可利用性结论）\n"
        "   ## 4. 处置与修复情况\n"
        "   （分诊 verify/update 结果、修复优先级与 SLA 状态、风险接受决策及理由、"
        "等待人工审批的事项）\n"
        "   ## 5. 风险评估与建议\n"
        "   （基于数据的整体风险判断、遗留风险、后续行动建议）\n"
        "   ## 6. 附录\n"
        "   （数据来源说明：DefectDojo 字段 vs KG 证据；数据缺失项清单）\n"
        "4. 章节内无数据时保留标题并写明“本次任务未涉及”。\n"
        "5. 直接输出 Markdown 正文，不要输出 JSON，不要额外解释。"
    ),
    expected_output="一份结构完整的中文 Markdown 漏洞分析报告",
    agent=report_agent,
)
