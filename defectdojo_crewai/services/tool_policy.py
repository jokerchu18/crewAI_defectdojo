from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from defectdojo_crewai.models.schemas import PendingApproval, WriteToolCall


RiskLevel = Literal["low", "medium", "high", "critical"]

_WRITE_TOOLS: dict[str, BaseTool] = {}
_CAPTURED_APPROVALS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "captured_write_approvals",
    default=None,
)
_CURRENT_WORKFLOW_ID: ContextVar[str | None] = ContextVar(
    "current_write_workflow_id",
    default=None,
)
_CURRENT_STEP_ID: ContextVar[str | None] = ContextVar(
    "current_write_step_id",
    default=None,
)


class ApprovalGatedTool(BaseTool):
    """Expose a write tool to an agent without granting execution permission."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    wrapped_tool: BaseTool = Field(exclude=True)
    requested_by: str
    risk_level: RiskLevel = "high"
    approval_title: str | None = None

    def _run(self, **kwargs: Any) -> dict[str, Any]:
        tool_call = WriteToolCall(
            tool_name=self.wrapped_tool.name,
            arguments=_validated_arguments(self.wrapped_tool, kwargs),
            requested_by=self.requested_by,
        )
        approval = request_write_tool_approval(
            [tool_call],
            title=self.approval_title or f"Approve {self.wrapped_tool.name}",
            description=(
                f"{self.requested_by} requested write tool "
                f"{self.wrapped_tool.name}."
            ),
            risk_level=self.risk_level,
            workflow_id=_CURRENT_WORKFLOW_ID.get(),
            step_id=_CURRENT_STEP_ID.get(),
            requested_by=self.requested_by,
        )
        captured = _CAPTURED_APPROVALS.get()
        if captured is not None:
            captured.append(approval)
        return {
            "status": "waiting_approval",
            "approval_id": approval["approval_id"],
            "tool_call_id": tool_call.tool_call_id,
            "tool_name": tool_call.tool_name,
        }


def gated_write_tool(
    tool: BaseTool,
    *,
    requested_by: str,
    risk_level: RiskLevel = "high",
    approval_title: str | None = None,
) -> ApprovalGatedTool:
    register_write_tool(tool)
    return ApprovalGatedTool(
        name=tool.name,
        description=(
            f"{tool.description} This is a write operation and requires approval "
            "before execution."
        ),
        args_schema=tool.args_schema,
        wrapped_tool=tool,
        requested_by=requested_by,
        risk_level=risk_level,
        approval_title=approval_title,
    )


def register_write_tool(tool: BaseTool) -> None:
    existing = _WRITE_TOOLS.get(tool.name)
    if existing is not None and type(existing) is not type(tool):
        raise ValueError(f"Write tool name is already registered: {tool.name}")
    _WRITE_TOOLS[tool.name] = tool


def request_write_tool_approval(
    tool_calls: Sequence[WriteToolCall],
    *,
    title: str,
    description: str,
    risk_level: RiskLevel = "high",
    workflow_id: str | None = None,
    step_id: str | None = None,
    requested_by: str,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not tool_calls:
        raise ValueError("At least one write tool call is required.")

    from defectdojo_crewai.services.approval_service import request_approval

    _ensure_default_write_tools_registered()
    validated_tool_calls = [
        _validated_tool_call(tool_call) for tool_call in tool_calls
    ]
    payload = dict(extra_payload or {})
    payload["tool_calls"] = [
        tool_call.model_dump() for tool_call in validated_tool_calls
    ]
    return request_approval(
        PendingApproval(
            action_type="tool.execute",
            title=title,
            description=description,
            payload=payload,
            risk_level=risk_level,
            workflow_id=workflow_id,
            step_id=step_id,
            requested_by=requested_by,
        )
    )


def execute_write_tool_calls(payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_default_write_tools_registered()
    raw_tool_calls = payload.get("tool_calls") or []
    if not raw_tool_calls:
        raise ValueError("Approval payload does not contain write tool calls.")

    results = []
    for raw_tool_call in raw_tool_calls:
        tool_call = WriteToolCall.model_validate(raw_tool_call)
        tool = _WRITE_TOOLS.get(tool_call.tool_name)
        if tool is None:
            raise ValueError(f"Unregistered write tool: {tool_call.tool_name}")

        arguments = _validated_arguments(tool, tool_call.arguments)
        result = tool.run(**arguments)
        results.append(
            {
                "tool_call_id": tool_call.tool_call_id,
                "tool_name": tool_call.tool_name,
                "result": _serialize_result(result),
            }
        )

    return {"results": results}


@contextmanager
def capture_write_approvals(
    *,
    workflow_id: str | None = None,
    step_id: str | None = None,
) -> Iterator[list[dict[str, Any]]]:
    approvals: list[dict[str, Any]] = []
    approvals_token = _CAPTURED_APPROVALS.set(approvals)
    workflow_token = _CURRENT_WORKFLOW_ID.set(workflow_id)
    step_token = _CURRENT_STEP_ID.set(step_id)
    try:
        yield approvals
    finally:
        _CURRENT_STEP_ID.reset(step_token)
        _CURRENT_WORKFLOW_ID.reset(workflow_token)
        _CAPTURED_APPROVALS.reset(approvals_token)


def _validated_arguments(tool: BaseTool, arguments: dict[str, Any]) -> dict[str, Any]:
    schema = tool.args_schema
    if schema is None:
        return dict(arguments)
    validated = schema.model_validate(arguments)
    return validated.model_dump()


def _serialize_result(result: Any) -> Any:
    if isinstance(result, BaseModel):
        return result.model_dump()
    if result is None or isinstance(result, (bool, int, float, str, list, dict)):
        return result
    return str(result)


def _validated_tool_call(tool_call: WriteToolCall) -> WriteToolCall:
    tool = _WRITE_TOOLS.get(tool_call.tool_name)
    if tool is None:
        raise ValueError(f"Unregistered write tool: {tool_call.tool_name}")
    return tool_call.model_copy(
        update={
            "arguments": _validated_arguments(tool, tool_call.arguments),
        }
    )


def _ensure_default_write_tools_registered() -> None:
    from defectdojo_crewai.tools.defectdojo_api import (
        DefectDojoCreateApprovedRiskAcceptanceTool,
        DefectDojoCreateRiskAcceptanceTool,
        DefectDojoImportScanTool,
        DefectDojoUpdateFindingTool,
        DefectDojoUpdateRemediationTool,
        DefectDojoUpdateRiskAcceptanceTool,
        DefectDojoUpdateTriageTool,
        DefectDojoVerifyFindingTool,
    )

    tool_types = (
        DefectDojoImportScanTool,
        DefectDojoUpdateFindingTool,
        DefectDojoUpdateRemediationTool,
        DefectDojoUpdateTriageTool,
        DefectDojoUpdateRiskAcceptanceTool,
        DefectDojoVerifyFindingTool,
        DefectDojoCreateRiskAcceptanceTool,
        DefectDojoCreateApprovedRiskAcceptanceTool,
    )
    for tool_type in tool_types:
        tool = tool_type()
        if tool.name not in _WRITE_TOOLS:
            register_write_tool(tool)
