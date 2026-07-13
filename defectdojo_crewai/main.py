from crewai import Crew, Process
from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.crews.exc_crew import exc_crew
from defectdojo_crewai.crews.person_review import person_review
from defectdojo_crewai.tools.defectdojo_api import (
    defectdojo_import_scan_tool,
)
from defectdojo_crewai.agents.scan_import import scan_import_agent
from defectdojo_crewai.tasks.import_tasks import import_scan_task
from defectdojo_crewai.agents.triage import triage_agent
from defectdojo_crewai.tasks.triage_tasks import triage_task
from defectdojo_crewai.agents.remediation import remediation_agent
from defectdojo_crewai.tasks.remediation_tasks import remediation_task
from defectdojo_crewai.agents.risk_acceptance import risk_acceptance_review_agent, risk_acceptance_execute_agent
from defectdojo_crewai.tasks.risk_tasks import risk_acceptance_review_task,risk_acceptance_execute_task

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

    crew1 = Crew(
        agents=[scan_import_agent,remediation_agent,risk_acceptance_review_agent],
        tasks=[import_scan_task,remediation_task,risk_acceptance_review_task],
        process=Process.sequential,
        verbose=True,
    )

    inputs1={
        "base_url": settings.defectdojo_base_url,
        "api_key": settings.defectdojo_api_key,
        "engagement_id": settings.defectdojo_engagement_id,
        "scan_type": settings.default_scan_type,
        "file_path": settings.default_scan_file_path,
    }

    result1 = exc_crew(crew1, inputs1)

# 加入人工审核

    approved_candidates_payload = person_review(result1.pydantic.candidates)

    crew2 = Crew(
    agents=[risk_acceptance_execute_agent],
    tasks=[risk_acceptance_execute_task],
    process=Process.sequential,
    verbose=True,
    )

    inputs2=approved_candidates_payload

    if inputs2:
        result2 = exc_crew(crew2, inputs2)

if __name__ == "__main__":
    main()