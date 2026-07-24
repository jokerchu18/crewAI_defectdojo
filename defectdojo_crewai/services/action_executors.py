from defectdojo_crewai.services.action_registry import register_action
from defectdojo_crewai.services.risk_acceptance_actions import (
    build_risk_acceptance_tool_calls,
)
from defectdojo_crewai.services.tool_policy import execute_write_tool_calls


@register_action("tool.execute")
def execute_approved_tool_calls(payload: dict) -> dict:
    return execute_write_tool_calls(payload)


@register_action("risk_acceptance.execute")
def execute_legacy_risk_acceptance(payload: dict) -> dict:
    """Execute approvals created before write tool calls became generic."""
    candidates = payload.get("approved_candidates") or []
    tool_calls = build_risk_acceptance_tool_calls(
        candidates,
        requested_by="risk_acceptance_review_agent",
    )
    return execute_write_tool_calls(
        {"tool_calls": [tool_call.model_dump() for tool_call in tool_calls]}
    )
