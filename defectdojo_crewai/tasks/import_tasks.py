from crewai import Task
from defectdojo_crewai.agents.scan_import import scan_import_agent
from defectdojo_crewai.tools.defectdojo_api import ImportScanResult

import_scan_task = Task(
    description=(
        "将扫描报告导入 DefectDojo。"
        "DefectDojo 地址是 {base_url}，"
        "engagement_id 是 {engagement_id}，"
        "scan_type 是 {scan_type}，"
        "文件路径是 {file_path}。"
    ),
    expected_output="返回导入统计：created/closed/reactivated/untouched 数量,返回test_id",
    agent=scan_import_agent,
    output_pydantic=ImportScanResult,
)