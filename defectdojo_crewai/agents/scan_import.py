from crewai import Agent, Task

from defectdojo_crewai.config import llm_config
from defectdojo_crewai.services.tool_policy import gated_write_tool
from defectdojo_crewai.tools.defectdojo_api import DefectDojoImportScanTool

scan_import_agent = Agent(
    role="安全扫描报告导入专家",
    goal="准确解析各类安全扫描工具报告，将漏洞数据规范化导入DefectDojo",
    backstory="精通100+种安全扫描工具的输出格式...",
    tools=[
        gated_write_tool(
            DefectDojoImportScanTool(),
            requested_by="scan_import_agent",
            risk_level="high",
            approval_title="Approve DefectDojo scan import",
        )
    ],
    verbose=True,
    allow_delegation=True,
    llm=llm_config.getLLM(),
)

