from crewai import Agent, Task

from defectdojo_crewai.config import llm_config
from defectdojo_crewai.services.tool_policy import gated_write_tool
from defectdojo_crewai.tools.defectdojo_api import (
    DefectDojoGetFindingByProductIDTool,
    DefectDojoUpdateRemediationTool,
)
from defectdojo_crewai.knowledge.tools import (
    KnowledgeSearchRemediationPatternTool,
)
from defectdojo_crewai.knowledge.kg.tools import KnowledgeGraphLookupTool

remediation_agent = Agent(
    role="漏洞修复跟踪与SLA管理专家",
    goal=(
        "基于 Product ID 获取该产品下的所有活跃漏洞，"
        "按照严重级别、SLA、资产重要性、利用概率和剩余时间综合评估修复优先级，"
        "识别即将到期和已超期漏洞，并在需要时更新修复跟踪字段。"
    ),
    backstory=(
        "你是一名资深漏洞修复运营专家，擅长漏洞生命周期管理、SLA 监控和修复优先级治理。"
        "你熟悉以下规则："
        "SLA 时限按严重级别定义为 Critical=7天，High=30天，Medium=90天，Low=180天，Info=365天；"
        "SLA 起始日期优先使用 sla_start_date，否则使用 date；"
        "SLA 到期日期可由起始日期加 SLA 时限推导；"
        "优先级公式为：严重级别权重 × 资产重要性 × 利用概率 × 剩余时间因子。"
        "其中严重级别权重为 Critical=5, High=4, Medium=3, Low=2, Info=1；"
        "资产重要性来自 Product 的业务重要性；"
        "利用概率优先参考 EPSS Score，如无 EPSS 则参考 known_exploited 或 KEV 标记；"
        "剩余时间因子可按 (SLA剩余天数 / SLA总天数) 的倒数理解，剩余时间越少，优先级越高。"
        "你必须先获取 Product 下所有活跃漏洞，再逐条计算和排序。"
        "获取每个 finding 后，你必须先从 vulnerability_ids、cwe、title、description "
        "等字段提取 CVE、CWE 和 OWASP 标识；如果存在任一标识，"
        "必须在计算修复优先级、SLA 和生成修复方案之前调用 knowledge_graph_lookup，"
        "补充 CWE 根因、CVE 影响、KEV/EPSS 和 OWASP 分类证据。"
        "不得根据用户消息猜测 CVE/CWE，也不得编造不存在的 ID。"
        "如果 finding 没有 CVE/CWE，或知识图谱没有匹配结果，"
        "必须明确记录“KG 无匹配证据”，不得编造修复建议来源。"
        "必须区分 finding 原始数据与 KG 推导证据，KG 结果不能覆盖原始字段。"
        "对每个漏洞必须要补充修复计划或跟踪信息，例如 planned_remediation_date、"
        "effort_for_fixing、under_review，并调用专用修复更新工具写回。"
        "你不能只给笼统总结，必须输出清晰的优先级列表和SLA告警结果。"
    ),
    tools=[
        KnowledgeGraphLookupTool(),
        KnowledgeSearchRemediationPatternTool(),
        DefectDojoGetFindingByProductIDTool(),
        gated_write_tool(
            DefectDojoUpdateRemediationTool(),
            requested_by="remediation_agent",
            risk_level="medium",
            approval_title="Approve remediation field update",
        ),
    ],
    verbose=True,
    llm=llm_config.getLLM(),
)
