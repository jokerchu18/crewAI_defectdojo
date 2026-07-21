import logging
from typing import Any

from crewai import Crew, Process
from pydantic import RootModel

from defectdojo_crewai.agents.deduplication import deduplication_agent
from defectdojo_crewai.agents.remediation import remediation_agent
from defectdojo_crewai.agents.risk_acceptance import risk_acceptance_review_agent
from defectdojo_crewai.agents.router import router_agent
from defectdojo_crewai.agents.scan_import import scan_import_agent
from defectdojo_crewai.agents.triage import triage_agent
from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.models.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationContext,
    PendingApproval,
    RiskAcceptanceReviewResult,
    UserIntent,
    WorkflowPlan,
    WorkflowStep,
)
from defectdojo_crewai.services.approval_service import request_approval
from defectdojo_crewai.services.message_store import append_message
from defectdojo_crewai.services.knowledge_prompt import (
    prepare_task_with_knowledge,
)
from defectdojo_crewai.services.output_parser import parse_model_output
from defectdojo_crewai.services.progress_service import (
    begin_progress,
    finish_progress,
    set_progress_steps,
    update_progress_step,
)
from defectdojo_crewai.services.session_service import (
    get_session_context,
    save_session_context,
)
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


def parse_workflow_plan(user_message: str) -> WorkflowPlan:
    prepared_task = prepare_task_with_knowledge(router_task, user_message)
    crew = Crew(
        agents=[router_agent],
        tasks=[prepared_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
    )
    result = crew.kickoff(inputs={"user_message": user_message})
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
    return _validate_workflow_plan(plan)


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
        return WorkflowPlan(
            steps=[
                WorkflowStep(
                    step_id="step_1",
                    intent="unknown",
                    instruction=plan.message or "未识别到可执行操作。",
                )
            ],
            message=plan.message,
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


def _handle_chat_request(request: ChatRequest) -> ChatResponse:
    plan = parse_workflow_plan(request.message)
    set_progress_steps(
        request.session_id,
        [
            {"step_id": step.step_id, "intent": step.intent, "status": "pending"}
            for step in plan.steps
        ],
    )
    context = _merge_context(
        get_session_context(request.session_id),
        request.context,
    )
    step_results: list[dict[str, Any]] = []
    completed_step_ids: set[str] = set()
    representative_intent = UserIntent(
        intent="unknown",
        message=plan.message or "未生成可执行步骤。",
    )
    workflow_status = "completed"

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
        result = _execute_intent(intent, request.session_id)
        step_results.append(_step_result(step, result))
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
    return ChatResponse(
        session_id=request.session_id,
        intent=representative_intent,
        plan=plan,
        context=context,
        result={
            "status": workflow_status,
            "steps": step_results,
            "message": final_message,
        },
    )


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


def _execute_intent(intent: UserIntent, session_id: str) -> dict[str, Any]:
    if intent.intent == "risk_acceptance":
        return _request_risk_acceptance(intent, session_id)
    if intent.intent == "deduplication":
        return _run_deduplication(intent)
    if intent.intent == "triage":
        return _run_triage(intent)
    if intent.intent == "remediation":
        return _run_remediation(intent)
    if intent.intent == "import_scan":
        return _run_import_scan(intent)
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

    return ConversationContext.model_validate(values)


def _run_crew(
    agent,
    task,
    inputs: dict[str, Any],
    knowledge_query: str,
    output_model=None,
) -> dict[str, Any]:
    prepared_task = prepare_task_with_knowledge(task, knowledge_query)
    crew = Crew(
        agents=[agent],
        tasks=[prepared_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
    )
    output = crew.kickoff(inputs=inputs)
    if output_model is not None:
        parsed = parse_model_output(output, output_model)
        return {"status": "completed", "output": parsed.model_dump()}
    return {"status": "completed", "output": str(output)}


def _run_triage(intent: UserIntent) -> dict[str, Any]:
    if intent.test_id is None:
        return {
            "status": "need_input",
            "message": "请提供需要分诊的 DefectDojo test_id。",
        }
    return _run_crew(
        triage_agent,
        triage_task,
        {"test_id": intent.test_id},
        knowledge_query=(
            intent.message
            or f"DefectDojo test {intent.test_id} 漏洞分诊、有效性与利用性评估"
        ),
    )


def _run_deduplication(intent: UserIntent) -> dict[str, Any]:
    if intent.test_id is None:
        return {
            "status": "need_input",
            "message": "请提供需要去重的 DefectDojo test_id。",
        }
    return _run_crew(
        deduplication_agent,
        deduplicate_request_task,
        {"test_id": intent.test_id},
        knowledge_query=(
            intent.message
            or f"DefectDojo test {intent.test_id} 漏洞去重规则"
        ),
    )


def _run_remediation(intent: UserIntent) -> dict[str, Any]:
    if intent.product_id is None:
        return {
            "status": "need_input",
            "message": "请提供需要生成修复计划的 DefectDojo product_id。",
        }
    return _run_crew(
        remediation_agent,
        remediation_request_task,
        {"product_id": intent.product_id},
        knowledge_query=(
            intent.message
            or f"DefectDojo product {intent.product_id} 修复计划、优先级与 SLA"
        ),
    )


def _run_import_scan(intent: UserIntent) -> dict[str, Any]:
    inputs = {
        "base_url": settings.defectdojo_base_url,
        "engagement_id": intent.engagement_id or settings.defectdojo_engagement_id,
        "scan_type": intent.scan_type or settings.default_scan_type,
        "file_path": intent.file_path or settings.default_scan_file_path,
    }
    return _run_crew(
        scan_import_agent,
        import_scan_task,
        inputs,
        knowledge_query=(
            intent.message
            or f"{inputs['scan_type']} 扫描报告导入 DefectDojo"
        ),
        output_model=ImportScanResult,
    )


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
    session_id: str,
) -> dict[str, Any]:
    if intent.product_id is None:
        return {
            "status": "need_input",
            "message": "请在请求中提供 Product ID，例如：评估 Product 1 的风险接受。",
        }

    knowledge_query = (
        intent.message
        or (
            f"DefectDojo product {intent.product_id} 风险接受评估，"
            f"严重级别 {intent.severity or 'Medium, Low, Info'}"
        )
    )
    prepared_task = prepare_task_with_knowledge(
        risk_acceptance_request_task,
        knowledge_query,
    )
    crew = Crew(
        agents=[risk_acceptance_review_agent],
        tasks=[prepared_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
    )
    result = crew.kickoff(
        inputs={
            "product_id": intent.product_id,
            "severity_filter": intent.severity or "Medium, Low, Info",
        }
    )
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
        }

    approval = request_approval(
        PendingApproval(
            action_type="risk_acceptance.execute",
            title=f"Product {intent.product_id} 风险接受审批",
            description="风险预审 Agent 建议接受以下 findings，需要人工确认后执行。",
            payload={
                "product_id": intent.product_id,
                "approved_candidates": accept_candidates,
            },
            risk_level="high",
            workflow_id=session_id,
            requested_by="risk_acceptance_review_agent",
        )
    )

    return {
        "status": "waiting_approval",
        "message": "以下 findings 被建议 Accept，请人工审批。",
        "approval_id": approval["approval_id"],
        "candidates": accept_candidates,
    }
