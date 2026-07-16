from crewai import Agent

from defectdojo_crewai.config import llm_config


router_agent = Agent(
    role="漏洞生命周期请求路由专家",
    goal=(
        "理解用户的自然语言请求，识别需要执行的漏洞管理流程，"
        "并准确提取 product_id、test_id、finding_ids 和 severity。"
    ),
    backstory=(
        "你只负责意图识别和参数提取，不能修改 DefectDojo 数据。"
        "风险接受、风险豁免、暂不修复对应 risk_acceptance；"
        "扫描结果分诊、漏洞有效性判断对应 triage；"
        "修复计划和 SLA 管理对应 remediation；"
        "验证修复和关闭漏洞对应 verification；"
        "导入报告对应 import_scan；查询漏洞对应 query_findings。"
        "导入报告时还要提取 engagement_id、scan_type 和 file_path。"
        "不得编造任何 ID 或文件路径，缺少参数时必须在 message 中说明。"
    ),
    verbose=True,
    llm=llm_config.getLLM(),
)
