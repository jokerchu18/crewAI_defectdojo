from crewai import Crew, Process
from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.tools.defectdojo_api import (
    defectdojo_import_scan_tool,
)
from defectdojo_crewai.agents.scan_import import scan_import_agent
from defectdojo_crewai.tasks.import_tasks import import_scan_task
from defectdojo_crewai.agents.triage import triage_agent
from defectdojo_crewai.tasks.triage_tasks import triage_task
from defectdojo_crewai.agents.remediation import remediation_agent
from defectdojo_crewai.tasks.remediation_tasks import remediation_task



def main():

    # scan_types_result = defectdojo_get_scan_types_tool(
    #     base_url=base_url,
    #     api_key=api_key,
    # )
    # print("Supported scan types:")
    # print(scan_types_result["scan_types"])

    # import_result = defectdojo_import_scan_tool(
    #     base_url=base_url,
    #     api_key=api_key,
    #     scan_type=scan_type,
    #     engagement_id=engagement_id,
    #     scan_file_path=scan_file_path,
    # )
    # print("Import result:")
    # print(import_result)

    crew = Crew(
        agents=[scan_import_agent,remediation_agent],
        tasks=[import_scan_task,remediation_task],
        process=Process.sequential,
        verbose=True,
    )

    result = crew.kickoff(
        inputs={
            "base_url": settings.defectdojo_base_url,
            "api_key": settings.defectdojo_api_key,
            "engagement_id": settings.defectdojo_engagement_id,
            "scan_type": settings.default_scan_type,
            "file_path": settings.default_scan_file_path,
        }
    )

    print(result)

if __name__ == "__main__":
    main()