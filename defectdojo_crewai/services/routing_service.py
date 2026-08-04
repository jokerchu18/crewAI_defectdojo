import logging
from typing import Any
from uuid import uuid4

from crewai import Crew, Process
from pydantic import RootModel

from defectdojo_crewai.agents.deduplication import deduplication_agent
from defectdojo_crewai.agents.remediation import remediation_agent
from defectdojo_crewai.agents.risk_acceptance import risk_acceptance_review_agent
from defectdojo_crewai.agents.router import router_agent
from defectdojo_crewai.agents.scan_import import scan_import_agent
from defectdojo_crewai.agents.triage import triage_agent
from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.memory.agent_output import capture_agent_execution
from defectdojo_crewai.memory.context_builder import (
    append_workflow_result,
    build_agent_context,
    load_memory_snapshot,
    prepare_task_with_context,
    workflow_step_from_result,
)
from defectdojo_crewai.memory.models import AgentContext, WorkflowContext
from defectdojo_crewai.models.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationContext,
    RiskAcceptanceReviewResult,
    UserIntent,
    WorkflowPlan,
    WorkflowStep,
)
from defectdojo_crewai.services.message_store import append_message
from defectdojo_crewai.knowledge.events import enqueue_router_outcome
from defectdojo_crewai.services.output_parser import parse_model_output
from defectdojo_crewai.services.risk_acceptance_actions import (
    build_risk_acceptance_tool_calls,
)
from defectdojo_crewai.services.progress_service import (
    begin_progress,
    finish_progress,
    set_progress_steps,
    update_progress_step,
)
from defectdojo_crewai.knowledge.router_fallback import annotate_router_fallback
from defectdojo_crewai.services.session_service import (
    get_session_context,
    save_session_context,
)
from defectdojo_crewai.services.tool_policy import (
    capture_write_approvals,
    request_write_tool_approval,
)
from defectdojo_crewai.services.approval_store import list_step_approvals
from defectdojo_crewai.services.workflow_store import (
    WorkflowRun,
    claim_workflow_resume,
    create_workflow_run,
    get_workflow_run,
    save_workflow_run,
    set_workflow_status,
)
from defectdojo_crewai.utils.retry import (
    AgentTimeoutError,
    execute_agent_with_timeout,
)
from defectdojo_crewai.utils.timeout_configs import AGENT_TIMEOUTS
from defectdojo_crewai.tasks.import_tasks import import_scan_task
from defectdojo_crewai.tasks.dedupe_tasks import deduplicate_request_task
from defectdojo_crewai.tasks.remediation_tasks import remediation_request_task
from defectdojo_crewai.tasks.risk_tasks import risk_acceptance_request_task
from defectdojo_crewai.tasks.router_tasks import router_task
from defectdojo_crewai.tasks.triage_tasks import triage_task
from defectdojo_crewai.tools.defectdojo_api import (
    ImportScanResult,
    defectdojo_get_finding_by_product_tool,
    defectdojo_get_finding_tool,
)


class _WorkflowStepList(RootModel[list[WorkflowStep]]):
    """Recovers a bare steps array when the surrounding plan JSON is broken."""


def parse_workflow_plan(
    user_message: str,
    *,
    agent_context: AgentContext | None = None,
) -> WorkflowPlan:
    prepared_router_task = prepare_task_with_context(router_task, agent_context)
    crew = Crew(
        agents=[router_agent],
        tasks=[prepared_router_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
    )
    router_config = AGENT_TIMEOUTS["router"]
    result = execute_agent_with_timeout(
        "router",
        router_config,
        crew.kickoff,
        inputs={"user_message": user_message},
    )
    try:
        plan = parse_model_output(result, WorkflowPlan)
    except ValueError:
        plan = _recover_workflow_plan(result)
    if not plan.steps:
        # A syntactically valid but empty plan usually means the router's JSON
        # was malformed and a sub-fragment slipped through; try recovery before
        # giving up so multi-step workflows are not silently dropped.
        recovered = _try_recover_workflow_plan(result)
        if recovered is not None:
            plan = recovered
    validated_plan = _validate_workflow_plan(plan)
    return annotate_router_fallback(validated_plan, user_message)


def _recover_workflow_plan(result: Any) -> WorkflowPlan:
    recovered = _try_recover_workflow_plan(result)
    if recovered is not None:
        return recovered
    legacy_intent = parse_model_output(result, UserIntent)
    return WorkflowPlan(
        steps=[
            WorkflowStep(
                step_id="step_1",
                intent=legacy_intent.intent,
                product_id=legacy_intent.product_id,
                test_id=legacy_intent.test_id,
                finding_ids=legacy_intent.finding_ids,
                severity=legacy_intent.severity,
                engagement_id=legacy_intent.engagement_id,
                scan_type=legacy_intent.scan_type,
                file_path=legacy_intent.file_path,
                instruction=legacy_intent.message,
            )
        ],
        message=legacy_intent.message,
    )


def _try_recover_workflow_plan(result: Any) -> WorkflowPlan | None:
    try:
        steps = parse_model_output(result, _WorkflowStepList).root
    except ValueError:
        return None
    if not steps:
        return None
    return WorkflowPlan(steps=steps)


def parse_user_intent(user_message: str) -> UserIntent:
    """Backward-compatible helper for callers that still expect one intent."""
    plan = parse_workflow_plan(user_message)
    if not plan.steps:
        return UserIntent(intent="unknown", message=plan.message)
    return plan.steps[0].to_user_intent()


def _validate_workflow_plan(plan: WorkflowPlan) -> WorkflowPlan:
    if not plan.steps:
        return plan.model_copy(
            update={"steps": [
                WorkflowStep(
                    step_id="step_1",
                    intent="unknown",
                    instruction=plan.message or "未识别到可执行操作。",
                )
            ]},
        )

    seen: set[str] = set()
    for index, step in enumerate(plan.steps):
        if step.step_id in seen:
            raise ValueError(f"Duplicate workflow step_id: {step.step_id}")

        missing = [
            dependency
            for dependency in step.depends_on
            if dependency not in seen
        ]
        if missing:
            raise ValueError(
                f"Step {step.step_id} depends on unknown or later steps: "
                f"{', '.join(missing)}"
            )

        if step.intent == "risk_acceptance" and index != len(plan.steps) - 1:
            raise ValueError(
                "risk_acceptance must be the final workflow step because "
                "it may pause for human approval."
            )
        seen.add(step.step_id)

    return plan


def handle_chat_request(request: ChatRequest) -> ChatResponse:
    begin_progress(request.session_id)
    append_message(request.session_id, "user", request.message)
    try:
        response = _handle_chat_request(request)
    except Exception:
        finish_progress(request.session_id, "failed", "工作流执行失败，请查看服务日志。")
        try:
            append_message(
                request.session_id,
                "assistant",
                "工作流执行失败，请查看服务日志。",
                result={"status": "failed"},
            )
        except Exception:
            logging.exception("Failed to persist failure message to history")
        raise
    append_message(
        request.session_id,
        "assistant",
        str(response.result.get("message") or "工作流已处理。"),
        result=response.result,
    )
    return response


def _handle_chat_request_legacy(request: ChatRequest) -> ChatResponse:
    context = _merge_context(
        get_session_context(request.session_id),
        request.context,
    )
    memory_snapshot = load_memory_snapshot(
        request.session_id,
        request.message,
    )
    router_context = build_agent_context(
        current_request=request.message,
        target_agent="router",
        business_context=context,
        conversation_history=memory_snapshot.conversation_history,
        workflow_context=memory_snapshot.workflow_context,
    )
    plan = parse_workflow_plan(
        request.message,
        agent_context=router_context,
    )

    # 返回给前端工作状态和步骤
    set_progress_steps(
        request.session_id,
        [
            {"step_id": step.step_id, "intent": step.intent, "status": "pending"}
            for step in plan.steps
        ],
    )

    step_results: list[dict[str, Any]] = []
    workflow_memory = memory_snapshot.workflow_context.model_copy(deep=True)
    completed_step_ids: set[str] = set()
    representative_intent = UserIntent(
        intent="unknown",
        message=plan.message or "未生成可执行步骤。",
    )
    workflow_status = "completed"

    # 遍历处理工作流
    for index, step in enumerate(plan.steps):
        missing_dependencies = [
            dependency
            for dependency in step.depends_on
            if dependency not in completed_step_ids
        ]
        if missing_dependencies:
            step_result = {
                "status": "blocked",
                "message": (
                    f"步骤 {step.step_id} 的依赖尚未完成: "
                    f"{', '.join(missing_dependencies)}"
                ),
            }
            step_results.append(_step_result(step, step_result))
            update_progress_step(request.session_id, step.step_id, "blocked")
            workflow_status = "blocked"
            break

        intent = _merge_intent_context(
            step.to_user_intent(),
            context,
            request.context,
        )
        if index == 0:
            representative_intent = intent

        update_progress_step(request.session_id, step.step_id, "running")
        agent_context = build_agent_context(
            current_request=request.message,
            target_agent=intent.intent,
            business_context=context,
            conversation_history=memory_snapshot.conversation_history,
            workflow_context=workflow_memory,
        )
        result = _execute_intent(
            intent,
            request.session_id,
            agent_context,
        )
        recorded_step = _step_result(step, result)
        step_results.append(recorded_step)
        append_workflow_result(
            workflow_memory,
            workflow_id=request.session_id,
            step=recorded_step,
        )

        # 更新Context，即各种id 
        context = _updated_context(intent, result, base=context)

        status = result.get("status", "completed")
        update_progress_step(request.session_id, step.step_id, status)
        if status == "completed":
            completed_step_ids.add(step.step_id)
            continue

        workflow_status = status
        break

    save_session_context(request.session_id, context)
    final_message = _workflow_message(workflow_status, plan.message, step_results)
    finish_progress(request.session_id, workflow_status, final_message)
    enqueue_router_outcome(
        workflow_id=request.session_id,
        user_input=request.message,
        plan=plan.model_dump(),
        outcome=workflow_status,
    )
    
    return ChatResponse(
        session_id=request.session_id,
        intent=representative_intent,
        plan=plan,
        context=context,
        result={
            "status": workflow_status,
            "steps": step_results,
            "message": final_message,
            "plan": plan.model_dump(mode="json"),
        },
    )


def _handle_chat_request(request: ChatRequest) -> ChatResponse:
    context = _merge_context(
        get_session_context(request.session_id),
        request.context,
    )
    memory_snapshot = load_memory_snapshot(
        request.session_id,
        request.message,
    )
    router_context = build_agent_context(
        current_request=request.message,
        target_agent="router",
        business_context=context,
        conversation_history=memory_snapshot.conversation_history,
        workflow_context=memory_snapshot.workflow_context,
    )
    plan = parse_workflow_plan(
        request.message,
        agent_context=router_context,
    )
    run = create_workflow_run(
        WorkflowRun(
            workflow_id=str(uuid4()),
            session_id=request.session_id,
            plan=plan,
            context=context,
            explicit_context=request.context,
            conversation_history=memory_snapshot.conversation_history,
            workflow_context=memory_snapshot.workflow_context,
            user_message=request.message,
            representative_intent=UserIntent(
                intent="unknown",
                message=plan.message or "No executable workflow step was generated.",
            ).model_dump(mode="json"),
        )
    )
    _set_run_progress(run)
    return _continue_workflow(run)


def _continue_workflow(run: WorkflowRun) -> ChatResponse:
    context = run.context.model_copy(deep=True)
    workflow_memory = run.workflow_context.model_copy(deep=True)
    completed_step_ids = set(run.completed_step_ids)
    step_results = list(run.step_results)
    representative_intent = UserIntent.model_validate(
        run.representative_intent
        or {"intent": "unknown", "message": run.plan.message}
    )
    workflow_status = "completed"

    for index in range(run.current_step_index, len(run.plan.steps)):
        step = run.plan.steps[index]
        missing_dependencies = [
            dependency
            for dependency in step.depends_on
            if dependency not in completed_step_ids
        ]
        if missing_dependencies:
            step_result = {
                "status": "blocked",
                "message": (
                    f"Step {step.step_id} has incomplete dependencies: "
                    f"{', '.join(missing_dependencies)}"
                ),
            }
            step_results.append(_step_result(step, step_result))
            update_progress_step(run.session_id, step.step_id, "blocked")
            workflow_status = "blocked"
            run = save_workflow_run(
                run.model_copy(
                    deep=True,
                    update={
                        "status": "blocked",
                        "current_step_index": index,
                        "completed_step_ids": list(completed_step_ids),
                        "context": context,
                        "workflow_context": workflow_memory,
                        "step_results": step_results,
                        "representative_intent": representative_intent.model_dump(
                            mode="json"
                        ),
                    },
                )
            )
            break

        intent = _merge_intent_context(
            step.to_user_intent(),
            context,
            run.explicit_context,
        )
        if index == 0:
            representative_intent = intent

        update_progress_step(run.session_id, step.step_id, "running")
        agent_context = build_agent_context(
            current_request=run.user_message,
            target_agent=intent.intent,
            business_context=context,
            conversation_history=run.conversation_history,
            workflow_context=workflow_memory,
        )
        result = _execute_intent(
            intent,
            run.workflow_id,
            step.step_id,
            agent_context,
        )
        recorded_step = _step_result(step, result)
        step_results.append(recorded_step)
        append_workflow_result(
            workflow_memory,
            workflow_id=run.workflow_id,
            step=recorded_step,
        )
        context = _updated_context(intent, result, base=context)

        status = result.get("status", "completed")
        update_progress_step(run.session_id, step.step_id, status)
        if status == "completed":
            completed_step_ids.add(step.step_id)
            run = save_workflow_run(
                run.model_copy(
                    deep=True,
                    update={
                        "status": "running",
                        "current_step_index": index + 1,
                        "completed_step_ids": list(completed_step_ids),
                        "context": context,
                        "workflow_context": workflow_memory,
                        "step_results": step_results,
                        "representative_intent": representative_intent.model_dump(
                            mode="json"
                        ),
                    },
                )
            )
            continue

        workflow_status = status
        run = save_workflow_run(
            run.model_copy(
                deep=True,
                update={
                    "status": status,
                    "current_step_index": index,
                    "completed_step_ids": list(completed_step_ids),
                    "context": context,
                    "workflow_context": workflow_memory,
                    "step_results": step_results,
                    "representative_intent": representative_intent.model_dump(
                        mode="json"
                    ),
                },
            )
        )
        break

    if workflow_status == "completed":
        run = save_workflow_run(
            run.model_copy(
                deep=True,
                update={
                    "status": "completed",
                    "current_step_index": len(run.plan.steps),
                    "completed_step_ids": list(completed_step_ids),
                    "context": context,
                    "workflow_context": workflow_memory,
                    "step_results": step_results,
                    "representative_intent": representative_intent.model_dump(
                        mode="json"
                    ),
                },
            )
        )

    save_session_context(run.session_id, context)
    final_message = _workflow_message(
        workflow_status,
        run.plan.message,
        step_results,
    )
    finish_progress(run.session_id, workflow_status, final_message)
    enqueue_router_outcome(
        workflow_id=run.workflow_id,
        user_input=run.user_message,
        plan=run.plan.model_dump(),
        outcome=workflow_status,
    )
    return ChatResponse(
        session_id=run.session_id,
        intent=representative_intent,
        plan=run.plan,
        context=context,
        result={
            "status": workflow_status,
            "workflow_id": run.workflow_id,
            "steps": step_results,
            "message": final_message,
            "plan": run.plan.model_dump(mode="json"),
        },
    )


def resume_workflow(workflow_id: str) -> ChatResponse | None:
    waiting_run = get_workflow_run(workflow_id)
    if waiting_run is None or waiting_run.status != "waiting_approval":
        return None
    if waiting_run.current_step_index >= len(waiting_run.plan.steps):
        return None

    step = waiting_run.plan.steps[waiting_run.current_step_index]
    approvals = list_step_approvals(workflow_id, step.step_id)
    if not approvals or any(
        approval["status"] != "completed" for approval in approvals
    ):
        return None

    run = claim_workflow_resume(workflow_id)
    if run is None:
        return None

    try:
        _set_run_progress(run)
        result = _completed_approval_result(
            run.step_results[-1].get("result", {}),
            approvals,
        )
        recorded_step = _step_result(step, result)
        step_results = list(run.step_results)
        if step_results and step_results[-1].get("step_id") == step.step_id:
            step_results[-1] = recorded_step
        else:
            step_results.append(recorded_step)

        intent = _merge_intent_context(
            step.to_user_intent(),
            run.context,
            run.explicit_context,
        )
        context = _updated_context(intent, result, base=run.context)
        completed_step_ids = set(run.completed_step_ids)
        completed_step_ids.add(step.step_id)
        workflow_context = _replace_workflow_step(
            run.workflow_context,
            workflow_id=run.workflow_id,
            step=recorded_step,
        )
        update_progress_step(run.session_id, step.step_id, "completed")
        run = save_workflow_run(
            run.model_copy(
                deep=True,
                update={
                    "status": "running",
                    "current_step_index": run.current_step_index + 1,
                    "completed_step_ids": list(completed_step_ids),
                    "context": context,
                    "workflow_context": workflow_context,
                    "step_results": step_results,
                },
            )
        )
        response = _continue_workflow(run)
        append_message(
            run.session_id,
            "assistant",
            str(response.result.get("message") or "Workflow resumed."),
            result=response.result,
        )
        return response
    except Exception:
        set_workflow_status(
            workflow_id,
            "failed",
            only_if=("resuming", "running"),
        )
        finish_progress(run.session_id, "failed", "Workflow resume failed.")
        raise


def reject_workflow(workflow_id: str | None) -> None:
    if not workflow_id:
        return
    run = get_workflow_run(workflow_id)
    if run is not None and set_workflow_status(workflow_id, "rejected"):
        finish_progress(run.session_id, "rejected", "Workflow approval was rejected.")


def fail_workflow(workflow_id: str | None) -> None:
    if not workflow_id:
        return
    run = get_workflow_run(workflow_id)
    if run is not None and set_workflow_status(workflow_id, "failed"):
        finish_progress(run.session_id, "failed", "Approved write operation failed.")


def _set_run_progress(run: WorkflowRun) -> None:
    begin_progress(run.session_id, "Restoring workflow progress...")
    completed = set(run.completed_step_ids)
    set_progress_steps(
        run.session_id,
        [
            {
                "step_id": step.step_id,
                "intent": step.intent,
                "status": (
                    "completed"
                    if step.step_id in completed
                    else (
                        "waiting_approval"
                        if index == run.current_step_index
                        and run.status in {"waiting_approval", "resuming"}
                        else "pending"
                    )
                ),
            }
            for index, step in enumerate(run.plan.steps)
        ],
    )


def _completed_approval_result(
    original_result: dict[str, Any],
    approvals: list[dict[str, Any]],
) -> dict[str, Any]:
    merged = dict(original_result)
    tool_results = []
    approval_results = []
    for approval in approvals:
        result = approval.get("result")
        result = result if isinstance(result, dict) else {}
        current_results = result.get("results")
        if isinstance(current_results, list):
            tool_results.extend(
                item for item in current_results if isinstance(item, dict)
            )
        approval_results.append(
            {
                "approval_id": approval["approval_id"],
                "result": result,
            }
        )
    merged.update(
        {
            "status": "completed",
            "message": "Approved write operations completed; workflow resumed.",
            "results": tool_results,
            "approval_results": approval_results,
        }
    )
    return merged


def _replace_workflow_step(
    workflow_context: WorkflowContext,
    *,
    workflow_id: str,
    step: dict[str, Any],
) -> WorkflowContext:
    updated = workflow_context.model_copy(deep=True)
    replacement = workflow_step_from_result(
        workflow_id=workflow_id,
        step=step,
        source="current",
    )
    for index in range(len(updated.steps) - 1, -1, -1):
        existing = updated.steps[index]
        if (
            existing.workflow_id == workflow_id
            and existing.step_id == replacement.step_id
        ):
            updated.steps[index] = replacement
            return updated
    updated.steps.append(replacement)
    return updated


_INTENT_LABELS = {
    "risk_acceptance": "风险接受",
    "deduplication": "去重",
    "triage": "分诊",
    "remediation": "修复计划",
    "verification": "修复验证",
    "import_scan": "报告导入",
    "query_findings": "漏洞查询",
    "unknown": "未识别操作",
}

_STATUS_LABELS = {
    "completed": "已完成",
    "waiting_approval": "等待人工审批",
    "need_input": "需要补充信息",
    "blocked": "被依赖阻塞",
    "not_implemented": "尚未实现",
    "unknown": "无法识别",
    "failed": "执行失败",
}


def _workflow_message(
    workflow_status: str,
    plan_message: str,
    step_results: list[dict[str, Any]],
) -> str:
    """Build a human-readable summary so the UI always has a final answer."""
    lines: list[str] = []
    overall = _STATUS_LABELS.get(workflow_status, workflow_status)
    lines.append(f"工作流{overall}（共 {len(step_results)} 个步骤）。")
    if plan_message:
        lines.append(plan_message)

    for entry in step_results:
        label = _INTENT_LABELS.get(entry["intent"], entry["intent"])
        status = _STATUS_LABELS.get(entry["status"], entry["status"])
        detail = _step_summary(entry.get("result") or {})
        line = f"[{entry['step_id']}] {label}：{status}"
        if detail:
            line = f"{line}。{detail}"
        lines.append(line)

    return "\n".join(lines)


def _step_summary(result: dict[str, Any]) -> str:
    if result.get("message"):
        return str(result["message"])

    output = result.get("output")
    if isinstance(output, str):
        return output.strip()
    if isinstance(output, dict):
        parts = [
            f"{key}={output[key]}"
            for key in ("test_id", "product_id", "engagement_id")
            if output.get(key) is not None
        ]
        if parts:
            return "关键结果: " + ", ".join(parts)
        return ""

    findings = result.get("findings")
    if isinstance(findings, dict):
        count = len(findings.get("results") or [])
        return f"共查询到 {count} 个 findings。"
    return ""


def _execute_intent(
    intent: UserIntent,
    workflow_id: str,
    step_id: str,
    agent_context: AgentContext | None = None,
) -> dict[str, Any]:
    if intent.intent == "risk_acceptance":
        return _request_risk_acceptance(
            intent,
            workflow_id,
            step_id,
            agent_context,
        )
    if intent.intent == "deduplication":
        return _run_deduplication(intent, workflow_id, step_id, agent_context)
    if intent.intent == "triage":
        return _run_triage(intent, workflow_id, step_id, agent_context)
    if intent.intent == "remediation":
        return _run_remediation(intent, workflow_id, step_id, agent_context)
    if intent.intent == "import_scan":
        return _run_import_scan(intent, workflow_id, step_id, agent_context)
    if intent.intent == "query_findings":
        return _query_findings(intent)
    if intent.intent == "verification":
        return {
            "status": "not_implemented",
            "message": "修复验证与关闭 Agent 尚未实现，未执行任何 DefectDojo 写操作。",
        }
    return {
        "status": "unknown",
        "message": intent.message or "无法识别请求，请补充要执行的漏洞管理操作。",
    }


def _step_result(step: WorkflowStep, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "intent": step.intent,
        "depends_on": step.depends_on,
        "status": result.get("status", "completed"),
        "result": result,
    }


def handle_user_message(
    user_message: str,
    *,
    session_id: str | None = None,
    context: ConversationContext | dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = ChatRequest(
        message=user_message,
        **({"session_id": session_id} if session_id else {}),
        context=context or ConversationContext(),
    )
    return handle_chat_request(request).model_dump()


def _merge_intent_context(
    intent: UserIntent,
    stored: ConversationContext,
    explicit: ConversationContext,
) -> UserIntent:
    merged = intent.model_dump()
    stored_values = stored.model_dump()
    explicit_values = explicit.model_dump()

    for field in ConversationContext.model_fields:
        current = merged.get(field)
        stored_value = stored_values.get(field)
        explicit_value = explicit_values.get(field)

        if _has_value(explicit_value):
            merged[field] = explicit_value
        elif not _has_value(current) and _has_value(stored_value):
            merged[field] = stored_value

    return UserIntent.model_validate(merged)


def _merge_context(
    stored: ConversationContext,
    explicit: ConversationContext,
) -> ConversationContext:
    values = stored.model_dump()
    for field, value in explicit.model_dump().items():
        if _has_value(value):
            values[field] = value
    return ConversationContext.model_validate(values)


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _updated_context(
    intent: UserIntent,
    result: dict[str, Any],
    *,
    base: ConversationContext | None = None,
) -> ConversationContext:
    values = (
        base.model_dump()
        if base is not None
        else ConversationContext().model_dump()
    )
    for field in ConversationContext.model_fields:
        value = getattr(intent, field)
        if _has_value(value):
            values[field] = value

    output = result.get("output")
    if isinstance(output, dict):
        for field in ("test_id", "product_id", "engagement_id"):
            if output.get(field) is not None:
                values[field] = output[field]

    findings = result.get("findings")
    if isinstance(findings, dict):
        finding_results = findings.get("results") or []
        finding_ids = [
            item["id"]
            for item in finding_results
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        ]
        if finding_ids:
            values["finding_ids"] = finding_ids

    candidates = result.get("candidates")
    if isinstance(candidates, list):
        candidate_ids = [
            item["finding_id"]
            for item in candidates
            if isinstance(item, dict) and isinstance(item.get("finding_id"), int)
        ]
        if candidate_ids:
            values["finding_ids"] = candidate_ids

    _collect_tool_context(result.get("results"), values)

    return ConversationContext.model_validate(values)


def _collect_tool_context(value: Any, context_values: dict[str, Any]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_tool_context(item, context_values)
        return
    if not isinstance(value, dict):
        return

    for field in ("test_id", "product_id", "engagement_id"):
        if value.get(field) is not None:
            context_values[field] = value[field]
    finding_ids = value.get("finding_ids")
    if isinstance(finding_ids, list) and finding_ids:
        context_values["finding_ids"] = [
            item for item in finding_ids if isinstance(item, int)
        ]
    for nested in value.values():
        if isinstance(nested, (dict, list)):
            _collect_tool_context(nested, context_values)


def _run_crew(
    agent,
    task,
    inputs: dict[str, Any],
    output_model=None,
    workflow_id: str | None = None,
    step_id: str | None = None,
    agent_context: AgentContext | None = None,
    *,
    agent_name: str = "default",
) -> dict[str, Any]:
    prepared_task = prepare_task_with_context(task, agent_context)
    crew = Crew(
        agents=[agent],
        tasks=[prepared_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
    )
    with capture_write_approvals(
        workflow_id=workflow_id,
        step_id=step_id,
    ) as approvals:
        agent_config = AGENT_TIMEOUTS.get(
            agent_name, AGENT_TIMEOUTS.get("triage", AGENT_TIMEOUTS["triage"])
        )
        try:
            output = execute_agent_with_timeout(
                agent_name,
                agent_config,
                crew.kickoff,
                inputs=inputs,
            )
        except AgentTimeoutError:
            return {
                "status": "failed",
                "message": (
                    f"Agent {agent_name} 执行超时"
                    f"（{agent_config.agent_timeout:.0f}s），已中止。"
                ),
            }
    execution = capture_agent_execution(output, agent, prepared_task).model_dump(
        mode="json"
    )
    if approvals:
        return {
            "status": "waiting_approval",
            "message": "One or more write tool calls are waiting for approval.",
            "approval_id": approvals[0]["approval_id"],
            "approval_ids": [
                approval["approval_id"] for approval in approvals
            ],
            "output": str(output),
            "agent_execution": execution,
        }
    if output_model is not None:
        parsed = parse_model_output(output, output_model)
        return {
            "status": "completed",
            "output": parsed.model_dump(),
            "agent_execution": execution,
        }
    return {
        "status": "completed",
        "output": str(output),
        "agent_execution": execution,
    }


def _run_triage(
    intent: UserIntent,
    workflow_id: str | None = None,
    step_id: str | None = None,
    agent_context: AgentContext | None = None,
) -> dict[str, Any]:
    if intent.test_id is None:
        return {
            "status": "need_input",
            "message": "请提供需要分诊的 DefectDojo test_id。",
        }
    return _run_crew(
        triage_agent,
        triage_task,
        {"test_id": intent.test_id},
        workflow_id=workflow_id,
        step_id=step_id,
        agent_context=agent_context,
        agent_name="triage",
    )


def _run_deduplication(
    intent: UserIntent,
    workflow_id: str | None = None,
    step_id: str | None = None,
    agent_context: AgentContext | None = None,
) -> dict[str, Any]:
    if intent.test_id is None:
        return {
            "status": "need_input",
            "message": "请提供需要去重的 DefectDojo test_id。",
        }
    return _run_crew(
        deduplication_agent,
        deduplicate_request_task,
        {"test_id": intent.test_id},
        workflow_id=workflow_id,
        step_id=step_id,
        agent_context=agent_context,
        agent_name="deduplication",
    )


def _run_remediation(
    intent: UserIntent,
    workflow_id: str | None = None,
    step_id: str | None = None,
    agent_context: AgentContext | None = None,
) -> dict[str, Any]:
    if intent.product_id is None:
        return {
            "status": "need_input",
            "message": "请提供需要生成修复计划的 DefectDojo product_id。",
        }
    return _run_crew(
        remediation_agent,
        remediation_request_task,
        {"product_id": intent.product_id},
        workflow_id=workflow_id,
        step_id=step_id,
        agent_context=agent_context,
        agent_name="remediation",
    )


def _run_import_scan(
    intent: UserIntent,
    workflow_id: str | None = None,
    step_id: str | None = None,
    agent_context: AgentContext | None = None,
) -> dict[str, Any]:
    inputs = {
        "base_url": settings.defectdojo_base_url,
        "engagement_id": intent.engagement_id or settings.defectdojo_engagement_id,
        "scan_type": intent.scan_type or settings.default_scan_type,
        "file_path": intent.file_path or settings.default_scan_file_path,
    }
    result = _run_crew(
        scan_import_agent,
        import_scan_task,
        inputs,
        output_model=ImportScanResult,
        workflow_id=workflow_id,
        step_id=step_id,
        agent_context=agent_context,
        agent_name="import_scan",
    )

    # ── on-demand CVE enrichment ──────────────────────────────────
    if result.get("status") == "completed":
        _enrich_kg_after_import(result)

    return result


def _enrich_kg_after_import(result: dict[str, Any]) -> None:
    """Extract CVEs from imported findings and add to knowledge graph.

    Runs fire-and-forget — failures are logged and never affect the import.
    """
    try:
        from defectdojo_crewai.knowledge.kg.enricher import enrich_graph_from_scan
    except Exception:
        return

    output = result.get("output")
    if not isinstance(output, dict):
        return
    try:
        added = enrich_graph_from_scan(output)
        if added:
            logging.info("KG enriched with %d CVE(s) from scan import.", added)
    except Exception:
        logging.exception("KG enrichment after import failed (non-fatal)")


def _query_findings(intent: UserIntent) -> dict[str, Any]:
    if intent.test_id is not None:
        findings = defectdojo_get_finding_tool(
            base_url=settings.defectdojo_base_url,
            api_key=settings.defectdojo_api_key,
            test_id=intent.test_id,
        )
    elif intent.product_id is not None:
        findings = defectdojo_get_finding_by_product_tool(
            base_url=settings.defectdojo_base_url,
            api_key=settings.defectdojo_api_key,
            product_id=intent.product_id,
        )
    else:
        return {
            "status": "need_input",
            "message": "查询漏洞时请提供 product_id 或 test_id。",
        }
    return {"status": "completed", "findings": findings}


def _request_risk_acceptance(
    intent: UserIntent,
    workflow_id: str,
    step_id: str,
    agent_context: AgentContext | None = None,
) -> dict[str, Any]:
    if intent.product_id is None:
        return {
            "status": "need_input",
            "message": "请在请求中提供 Product ID，例如：评估 Product 1 的风险接受。",
        }

    prepared_task = prepare_task_with_context(
        risk_acceptance_request_task,
        agent_context,
    )
    crew = Crew(
        agents=[risk_acceptance_review_agent],
        tasks=[prepared_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
    )
    result = execute_agent_with_timeout(
        "risk_acceptance",
        AGENT_TIMEOUTS["risk_acceptance"],
        crew.kickoff,
        inputs={
            "product_id": intent.product_id,
            "severity_filter": intent.severity or "Medium, Low, Info",
        },
    )
    execution = capture_agent_execution(
        result,
        risk_acceptance_review_agent,
        prepared_task,
    ).model_dump(mode="json")
    review_result = parse_model_output(result, RiskAcceptanceReviewResult)

    all_candidates = [
        candidate.model_dump()
        for candidate in review_result.candidates
    ]
    accept_candidates = [
        candidate
        for candidate in all_candidates
        if candidate["decision"] == "Accept"
    ]

    if not accept_candidates:
        return {
            "status": "completed",
            "message": "预审完成，没有发现需要人工审批的 Accept 候选项。",
            "review_results": all_candidates,
            "agent_execution": execution,
        }

    requested_by = "risk_acceptance_review_agent"
    tool_calls = build_risk_acceptance_tool_calls(
        accept_candidates,
        requested_by=requested_by,
    )
    approval = request_write_tool_approval(
        tool_calls,
        title=f"Product {intent.product_id} 风险接受审批",
        description=(
            "风险预审 Agent 建议接受以下 findings；批准后将执行已记录的"
            "创建与更新工具调用。"
        ),
        risk_level="high",
        workflow_id=workflow_id,
        step_id=step_id,
        requested_by=requested_by,
        extra_payload={
            "product_id": intent.product_id,
            "approved_candidates": accept_candidates,
        },
    )

    return {
        "status": "waiting_approval",
        "message": "以下 findings 被建议 Accept，请人工审批。",
        "approval_id": approval["approval_id"],
        "candidates": accept_candidates,
        "agent_execution": execution,
    }
