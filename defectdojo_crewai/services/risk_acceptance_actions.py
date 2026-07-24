from typing import Any

from defectdojo_crewai.models.schemas import WriteToolCall


def build_risk_acceptance_tool_calls(
    candidates: list[dict[str, Any]],
    *,
    requested_by: str,
) -> list[WriteToolCall]:
    tool_calls: list[WriteToolCall] = []
    for candidate in candidates:
        if candidate.get("decision") != "Accept":
            continue

        finding_id = candidate["finding_id"]
        tool_calls.extend(
            [
                WriteToolCall(
                    tool_name="defectdojo_create_approved_risk_acceptance_tool",
                    arguments={
                        "finding_id": finding_id,
                        "title": candidate["title"],
                        "reason": candidate["reason"],
                        "expiration_date": candidate["expiration_date"],
                        "reactivate_expired": candidate["reactivate_expired"],
                        "restart_sla_expired": candidate["restart_sla_expired"],
                    },
                    requested_by=requested_by,
                ),
                WriteToolCall(
                    tool_name="defectdojo_update_risk_acceptance_tool",
                    arguments={
                        "finding_id": finding_id,
                        "risk_accepted": True,
                        "active": False,
                    },
                    requested_by=requested_by,
                ),
            ]
        )
    return tool_calls
