from typing import Any

from defectdojo_crewai.models.schemas import ApprovalDecision, PendingApproval
from defectdojo_crewai.services.action_registry import execute_action
from defectdojo_crewai.services.approval_store import (
    cancel_pending_step_approvals,
    claim_pending_approval,
    create_approval,
    get_approval,
    list_pending_approvals,
    list_step_approvals,
    update_approval,
)
from defectdojo_crewai.services import action_executors as _action_executors
from defectdojo_crewai.knowledge.events import (
    enqueue_approved_execution,
)


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
        _stop_workflow(approval, "rejected")
        return get_approval(decision.approval_id)

    payload = decision.edited_payload or approval["payload"]
    if decision.edited_payload is not None:
        _validate_edited_tool_calls(approval["payload"], payload)
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
        enqueue_approved_execution(
            approval={
                **approval,
                "payload": payload,
                "reviewer": decision.reviewer,
            },
            result=result,
        )
    except Exception as exc:
        update_approval(
            decision.approval_id,
            status="failed",
            error_message=str(exc),
        )
        _stop_workflow(approval, "failed")
        raise

    completed = get_approval(decision.approval_id)
    resumed = _resume_workflow_if_ready(completed)
    if resumed is not None:
        completed["workflow_resume"] = resumed.model_dump(mode="json")
    return completed


def _stop_workflow(approval: dict[str, Any], status: str) -> None:
    workflow_id = approval.get("workflow_id")
    step_id = approval.get("step_id")
    if not workflow_id or not step_id:
        return

    cancel_pending_step_approvals(
        workflow_id,
        step_id,
        except_approval_id=approval["approval_id"],
    )
    from defectdojo_crewai.services.routing_service import (
        fail_workflow,
        reject_workflow,
    )

    if status == "rejected":
        reject_workflow(workflow_id)
    else:
        fail_workflow(workflow_id)


def _resume_workflow_if_ready(approval: dict[str, Any] | None):
    if approval is None:
        return None
    workflow_id = approval.get("workflow_id")
    step_id = approval.get("step_id")
    if not workflow_id or not step_id:
        return None

    approvals = list_step_approvals(workflow_id, step_id)
    if not approvals or any(item["status"] != "completed" for item in approvals):
        return None

    from defectdojo_crewai.services.routing_service import resume_workflow

    return resume_workflow(workflow_id)


def _filter_approved_findings(
    payload: dict[str, Any],
    approved_finding_ids: list[int],
) -> dict[str, Any]:
    candidates = payload.get("approved_candidates")
    tool_calls = payload.get("tool_calls")
    if not candidates and not tool_calls:
        return payload

    filtered = dict(payload)
    selected_ids = set(approved_finding_ids)

    if candidates:
        candidate_ids = {item["finding_id"] for item in candidates}
        if not selected_ids:
            selected_ids = candidate_ids
        invalid_ids = selected_ids - candidate_ids
        if invalid_ids:
            raise ValueError(
                f"Finding IDs are not part of this approval: {sorted(invalid_ids)}"
            )
        filtered["approved_candidates"] = [
            item for item in candidates if item["finding_id"] in selected_ids
        ]
        if not filtered["approved_candidates"]:
            raise ValueError("At least one finding must be approved.")

    if tool_calls and selected_ids:
        available_ids = set().union(
            *(_tool_call_finding_ids(tool_call) for tool_call in tool_calls)
        )
        invalid_ids = selected_ids - available_ids
        if invalid_ids:
            raise ValueError(
                f"Finding IDs are not part of this approval: {sorted(invalid_ids)}"
            )
        filtered["tool_calls"] = [
            tool_call
            for tool_call in tool_calls
            if _tool_call_finding_ids(tool_call) & selected_ids
        ]
        if not filtered["tool_calls"]:
            raise ValueError("At least one write tool call must be approved.")

    return filtered


def _tool_call_finding_ids(tool_call: dict[str, Any]) -> set[int]:
    arguments = tool_call.get("arguments") or {}
    finding_ids: set[int] = set()
    finding_id = arguments.get("finding_id")
    if isinstance(finding_id, int):
        finding_ids.add(finding_id)
    accepted_findings = arguments.get("accepted_findings") or []
    finding_ids.update(
        value for value in accepted_findings if isinstance(value, int)
    )
    return finding_ids


def _validate_edited_tool_calls(
    original_payload: dict[str, Any],
    edited_payload: dict[str, Any],
) -> None:
    original_calls = original_payload.get("tool_calls")
    if not original_calls:
        return

    edited_calls = edited_payload.get("tool_calls") or []
    original_identities = [
        (
            call.get("tool_call_id"),
            call.get("tool_name"),
            call.get("requested_by"),
        )
        for call in original_calls
    ]
    edited_identities = [
        (
            call.get("tool_call_id"),
            call.get("tool_name"),
            call.get("requested_by"),
        )
        for call in edited_calls
    ]
    if edited_identities != original_identities:
        raise ValueError(
            "Edited approval must preserve tool call IDs, names, and requesters."
        )
