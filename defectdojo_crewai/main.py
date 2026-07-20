import argparse

from crewai import Crew, Process
from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.crews.exc_crew import exc_crew
from defectdojo_crewai.crews.person_review import person_review
from defectdojo_crewai.agents.scan_import import scan_import_agent
from defectdojo_crewai.tasks.import_tasks import import_scan_task
from defectdojo_crewai.agents.remediation import remediation_agent
from defectdojo_crewai.tasks.remediation_tasks import remediation_task
from defectdojo_crewai.agents.risk_acceptance import risk_acceptance_review_agent, risk_acceptance_execute_agent
from defectdojo_crewai.tasks.risk_tasks import risk_acceptance_review_task,risk_acceptance_execute_task

# def run_lifecycle():
#     crew1 = Crew(
#         agents=[scan_import_agent,remediation_agent,risk_acceptance_review_agent],
#         tasks=[import_scan_task,remediation_task,risk_acceptance_review_task],
#         process=Process.sequential,
#         verbose=True,
#     )

#     inputs1={
#         "base_url": settings.defectdojo_base_url,
#         "api_key": settings.defectdojo_api_key,
#         "engagement_id": settings.defectdojo_engagement_id,
#         "scan_type": settings.default_scan_type,
#         "file_path": settings.default_scan_file_path,
#     }

#     result1 = exc_crew(crew1, inputs1)

#     if result1.pydantic is None:
#         raise ValueError("风险接受预审没有返回结构化结果。")

#     approved_candidates_payload = person_review(result1.pydantic.candidates)
#     crew2 = Crew(
#         agents=[risk_acceptance_execute_agent],
#         tasks=[risk_acceptance_execute_task],
#         process=Process.sequential,
#         verbose=True,
#     )

#     if approved_candidates_payload:
#         result2 = exc_crew(
#             crew2,
#             {
#                 "human_approved": True,
#                 "approved_candidates": approved_candidates_payload,
#             },
#         )
#         print(result2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["chat", "web"],
        default="chat",
        help="chat 启动终端交互；web 启动浏览器界面与 HTTP API。",
    )
    args = parser.parse_args()

    if args.mode == "chat":
        from defectdojo_crewai.chat import run_chat

        run_chat()
        return

    if args.mode == "web":
        import uvicorn

        uvicorn.run(
            "defectdojo_crewai.web:app",
            host="127.0.0.1",
            port=8000,
            reload=False,
        )
        return

if __name__ == "__main__":
    main()
