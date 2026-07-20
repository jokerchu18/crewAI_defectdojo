
from crewai import Agent

from defectdojo_crewai.config import llm_config


deduplication_agent = Agent(
    role="漏洞去重智能体",
    goal="作为漏洞生命周期工作流中的去重处理节点",
    backstory="负责接收漏洞去重任务并返回处理结果。",
    verbose=True,
    llm=llm_config.getLLM(),
)

