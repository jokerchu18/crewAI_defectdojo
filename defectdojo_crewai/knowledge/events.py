"""Best-effort asynchronous indexing for trusted workflow outcomes."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.knowledge.storage import (
    SOURCE_AUDIT,
    SOURCE_REMEDIATION,
    SOURCE_TRIAGE,
)
from defectdojo_crewai.knowledge.retrieval import get_knowledge_store


LOGGER = logging.getLogger(__name__)
_INDEX_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="knowledge-index",
)

_TRIAGE_TOOLS = {
    "defectdojo_update_triage_tool",
    "defectdojo_update_risk_acceptance_tool",
    "defectdojo_create_risk_acceptance_tool",
    "defectdojo_create_approved_risk_acceptance_tool",
    "defectdojo_verify_finding_tool",
}
_SAFE_FIELDS = {
    "id",
    "finding_id",
    "title",
    "description",
    "severity",
    "severity_justification",
    "cwe",
    "vulnerability_ids",
    "component_name",
    "component_version",
    "file_path",
    "line",
    "active",
    "verified",
    "false_p",
    "out_of_scope",
    "risk_accepted",
    "is_mitigated",
    "mitigation",
    "fix_available",
    "fix_version",
    "planned_remediation_date",
    "planned_remediation_version",
    "effort_for_fixing",
    "epss_score",
    "epss_percentile",
    "known_exploited",
    "ransomware_used",
}


def enqueue_router_outcome(
    *,
    workflow_id: str,
    user_input: str,
    plan: dict[str, Any],
    outcome: str,
) -> None:
    if not settings.knowledge_enabled:
        return
    _INDEX_EXECUTOR.submit(
        _index_router_outcome,
        workflow_id,
        user_input,
        plan,
        outcome,
    )


def enqueue_approved_execution(
    *,
    approval: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if not settings.knowledge_enabled:
        return
    _INDEX_EXECUTOR.submit(
        _index_approved_execution,
        approval,
        result,
    )


def _index_router_outcome(
    workflow_id: str,
    user_input: str,
    plan: dict[str, Any],
    outcome: str,
) -> None:
    try:
        steps = plan.get("steps") or []
        intents = [str(step.get("intent")) for step in steps if step.get("intent")]
        text = (
            f"用户请求: {user_input}\n"
            f"工作流意图: {', '.join(intents) or 'unknown'}\n"
            f"执行结果: {outcome}\n"
            f"规划说明: {plan.get('message') or ''}"
        )
        get_knowledge_store().upsert_texts(
            texts=[text],
            source_type=SOURCE_AUDIT,
            source_id=workflow_id,
            metadata=[
                {
                    "source": f"workflow:{workflow_id}",
                    "workflow_id": workflow_id,
                    "intent": intents[0] if len(intents) == 1 else "multi_step",
                    "intents": intents,
                    "confidence": plan.get("confidence"),
                    "outcome": outcome,
                    "verification_status": "observed",
                }
            ],
        )
    except Exception:
        LOGGER.exception("Failed to index router outcome for %s", workflow_id)


def _index_approved_execution(
    approval: dict[str, Any],
    result: dict[str, Any],
) -> None:
    try:
        tool_results = {
            item.get("tool_call_id"): item.get("result")
            for item in (result.get("results") or [])
        }
        for tool_call in approval.get("payload", {}).get("tool_calls", []):
            tool_name = str(tool_call.get("tool_name") or "")
            arguments = dict(tool_call.get("arguments") or {})
            tool_result = tool_results.get(tool_call.get("tool_call_id"))
            source_type = _knowledge_source_type(
                tool_name,
                arguments,
                tool_result,
            )
            if source_type is None:
                continue

            safe_arguments = _safe_payload(arguments)
            safe_result = _safe_payload(
                tool_result if isinstance(tool_result, dict) else {}
            )
            finding_id = (
                safe_arguments.get("finding_id")
                or safe_result.get("finding_id")
                or safe_result.get("id")
            )
            source_id = (
                f"{approval.get('workflow_id') or approval['approval_id']}:"
                f"{tool_call['tool_call_id']}"
            )
            text = _tool_result_text(tool_name, safe_arguments, safe_result)
            get_knowledge_store().upsert_texts(
                texts=[text],
                source_type=source_type,
                source_id=source_id,
                metadata=[
                    {
                        "source": f"approval:{approval['approval_id']}",
                        "approval_id": approval["approval_id"],
                        "workflow_id": approval.get("workflow_id"),
                        "tool_call_id": tool_call["tool_call_id"],
                        "tool_name": tool_name,
                        "requested_by": tool_call.get("requested_by"),
                        "finding_id": finding_id,
                        "severity": safe_result.get("severity")
                        or safe_arguments.get("severity"),
                        "cwe_id": _string_value(
                            safe_result.get("cwe")
                            or safe_arguments.get("cwe")
                        ),
                        "verification_status": "approved_executed",
                    }
                ],
            )
    except Exception:
        LOGGER.exception(
            "Failed to index approved execution %s",
            approval.get("approval_id"),
        )


def _knowledge_source_type(
    tool_name: str,
    arguments: dict[str, Any],
    result: Any,
) -> str | None:
    result_values = result if isinstance(result, dict) else {}
    if tool_name in _TRIAGE_TOOLS:
        return SOURCE_TRIAGE
    if tool_name == "defectdojo_update_finding_tool":
        is_mitigated = arguments.get("is_mitigated")
        if is_mitigated is None:
            is_mitigated = result_values.get("is_mitigated")
        if is_mitigated is True:
            return SOURCE_REMEDIATION
        triage_fields = {
            "verified",
            "false_p",
            "out_of_scope",
            "risk_accepted",
            "severity_justification",
            "epss_score",
            "known_exploited",
        }
        if triage_fields & arguments.keys():
            return SOURCE_TRIAGE
    return None


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key in _SAFE_FIELDS and value is not None
    }


def _tool_result_text(
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> str:
    values = {**arguments, **result}
    lines = [f"已审批并执行的工具: {tool_name}"]
    for key in sorted(values):
        lines.append(f"{key}: {values[key]}")
    return "\n".join(lines)


def _string_value(value: Any) -> str | None:
    return str(value) if value is not None else None
