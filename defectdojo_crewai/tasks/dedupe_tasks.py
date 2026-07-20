from crewai import Task
from defectdojo_crewai.agents.deduplication import deduplication_agent
from defectdojo_crewai.tasks.import_tasks import import_scan_task

deduplicate_task = Task(
    description="根据import_scan_task导入结果中的 test_id，对对应 findings 执行去重",
    expected_output="去重结果：重复簇列表，每个簇的原始漏洞ID、重复漏洞ID列表、重复数量",
    agent=deduplication_agent,
    context=[import_scan_task],
)


deduplicate_request_task = Task(
    description=(
        "接收 DefectDojo test_id={test_id}，执行去重流程的基础框架。"
        "当前阶段不调用外部工具，不修改 DefectDojo 数据。"
    ),
    expected_output="包含 test_id 和当前框架执行状态的去重任务结果",
    agent=deduplication_agent,
)
