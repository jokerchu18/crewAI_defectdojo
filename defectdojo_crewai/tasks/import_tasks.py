from crewai import Task
from defectdojo_crewai.agents.scan_import import scan_import_agent
import_scan_task = Task(
    description=(
        "将扫描报告导入 DefectDojo。"
        "DefectDojo 地址是 {base_url}，"
        "engagement_id 是 {engagement_id}，"
        "scan_type 是 {scan_type}，"
        "文件路径是 {file_path}。"
        "完成工具调用后，只输出合法 JSON 对象，不要使用 Markdown 代码块；"
        "必须包含 stage、success、test_id、engagement_id、product_id。"
    ),
    expected_output="包含 stage、success、test_id、engagement_id、product_id 的 JSON",
    agent=scan_import_agent,
)
