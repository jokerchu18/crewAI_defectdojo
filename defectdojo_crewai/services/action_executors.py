from crewai import Crew, Process

from defectdojo_crewai.agents.risk_acceptance import risk_acceptance_execute_agent
from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.models.schemas import RiskAcceptanceExecutionResult
from defectdojo_crewai.services.action_registry import register_action
from defectdojo_crewai.services.output_parser import parse_model_output
from defectdojo_crewai.tasks.risk_tasks import risk_acceptance_execute_task


@register_action("risk_acceptance.execute")
def execute_risk_acceptance(payload: dict) -> dict:
    approved_candidates = payload.get("approved_candidates") or []
    if not approved_candidates:
        raise ValueError("No approved risk acceptance candidates were provided.")

    crew = Crew(
        agents=[risk_acceptance_execute_agent],
        tasks=[risk_acceptance_execute_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
    )

    result = crew.kickoff(
        inputs={
            "human_approved": True,
            "approved_candidates": approved_candidates,
        }
    )

    return parse_model_output(
        result,
        RiskAcceptanceExecutionResult,
    ).model_dump()
