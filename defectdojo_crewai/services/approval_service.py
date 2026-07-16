from typing import Any

from defectdojo_crewai.models.schemas import ApprovalDecision, PendingApproval
from defectdojo_crewai.services.action_registry import execute_action
from defectdojo_crewai.services.approval_store import (
    claim_pending_approval,
    create_approval,
    get_approval,
    list_pending_approvals,
    update_approval,
)
from defectdojo_crewai.services import action_executors as _action_executors


def request_approval(action: PendingApproval) -> dict[str, Any]:
    return create_approval(action)


def pending_approvals() -> list[dict[str, Any]]:
    return list_pending_approvals()


def decide_approval(decision: ApprovalDecision) -> dict[str, Any]:
    approval = get_approval(decision.approval_id)
    if approval is None:
        raise ValueError("Approval not found.")
    if approval["status"] != "pending":
        raise ValueError(f"Approval is already {approval['status']}.")

    if decision.decision == "reject":
        update_approval(
            decision.approval_id,
            status="rejected",
            reviewer=decision.reviewer,
            reviewer_comment=decision.comment,
        )
        return get_approval(decision.approval_id)

    payload = decision.edited_payload or approval["payload"]
    payload = _filter_approved_findings(payload, decision.approved_finding_ids)

    claimed = claim_pending_approval(
        decision.approval_id,
        reviewer=decision.reviewer,
        reviewer_comment=decision.comment,
        payload=payload,
    )
    if not claimed:
        current = get_approval(decision.approval_id)
        status = current["status"] if current else "missing"
        raise ValueError(f"Approval is already {status}.")

    try:
        result = execute_action(approval["action_type"], payload)
        update_approval(
            decision.approval_id,
            status="completed",
            result=result,
        )
    except Exception as exc:
        update_approval(
            decision.approval_id,
            status="failed",
            error_message=str(exc),
        )
        raise

    return get_approval(decision.approval_id)


def _filter_approved_findings(
    payload: dict[str, Any],
    approved_finding_ids: list[int],
) -> dict[str, Any]:
    candidates = payload.get("approved_candidates")
    if not candidates:
        return payload

    candidate_ids = {item["finding_id"] for item in candidates}
    selected_ids = set(approved_finding_ids) if approved_finding_ids else candidate_ids
    invalid_ids = selected_ids - candidate_ids
    if invalid_ids:
        raise ValueError(f"Finding IDs are not part of this approval: {sorted(invalid_ids)}")

    filtered = dict(payload)
    filtered["approved_candidates"] = [
        item for item in candidates if item["finding_id"] in selected_ids
    ]
    if not filtered["approved_candidates"]:
        raise ValueError("At least one finding must be approved.")
    return filtered
