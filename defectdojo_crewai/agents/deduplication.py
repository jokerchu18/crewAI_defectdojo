
from crewai import Agent, Task

from defectdojo_crewai.config import llm_config
from defectdojo_crewai.tools.defectdojo_api import DefectDojoDeduplicateTool


deduplication_agent = Agent(
    role="漏洞去重与聚合专家",
    goal="识别重复漏洞，构建漏洞簇，减少冗余数据",
    backstory="熟练掌握多种去重算法...",
    tools=[DefectDojoDeduplicateTool()],
    verbose=True,
    llm=llm_config.getLLM(),
)

