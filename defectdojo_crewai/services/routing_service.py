from typing import Any

from crewai import Crew, Process

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
)
from defectdojo_crewai.services.approval_service import request_approval
from defectdojo_crewai.services.output_parser import parse_model_output
from defectdojo_crewai.services.session_service import (
    get_session_context,
    save_session_context,
)
from defectdojo_crewai.tasks.import_tasks import import_scan_task
from defectdojo_crewai.tasks.remediation_tasks import remediation_request_task
from defectdojo_crewai.tasks.risk_tasks import risk_acceptance_request_task
from defectdojo_crewai.tasks.router_tasks import router_task
from defectdojo_crewai.tasks.triage_tasks import triage_task
from defectdojo_crewai.tools.defectdojo_api import (
    ImportScanResult,
    defectdojo_get_finding_by_product_tool,
    defectdojo_get_finding_tool,
)


def parse_user_intent(user_message: str) -> UserIntent:
    crew = Crew(
        agents=[router_agent],
        tasks=[router_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
    )
    result = crew.kickoff(inputs={"user_message": user_message})
    return parse_model_output(result, UserIntent)


def handle_chat_request(request: ChatRequest) -> ChatResponse:
    parsed_intent = parse_user_intent(request.message)
    stored_context = get_session_context(request.session_id)
    
    # REVIEW: request.context是否为null
    intent = _merge_intent_context(
        parsed_intent,
        stored_context,
        request.context,
    )

    if intent.intent == "risk_acceptance":
        result = _request_risk_acceptance(intent, request.session_id)
    elif intent.intent == "triage":
        result = _run_triage(intent)
    elif intent.intent == "remediation":
        result = _run_remediation(intent)
    elif intent.intent == "import_scan":
        result = _run_import_scan(intent)
    elif intent.intent == "query_findings":
        result = _query_findings(intent)
    elif intent.intent == "verification":
        result = {
            "status": "not_implemented",
            "message": "修复验证与关闭 Agent 尚未实现，未执行任何 DefectDojo 写操作。",
        }
    else:
        result = {
            "status": "unknown",
            "message": intent.message or "无法识别请求，请补充要执行的漏洞管理操作。",
        }

    context = _updated_context(intent, result)
    save_session_context(request.session_id, context)
    return ChatResponse(
        session_id=request.session_id,
        intent=intent,
        context=context,
        result=result,
    )


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


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _updated_context(
    intent: UserIntent,
    result: dict[str, Any],
) -> ConversationContext:
    values = {
        field: getattr(intent, field)
        for field in ConversationContext.model_fields
    }

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
    output_model=None,
) -> dict[str, Any]:
    crew = Crew(
        agents=[agent],
        tasks=[task],
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
    return _run_crew(triage_agent, triage_task, {"test_id": intent.test_id})


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

    crew = Crew(
        agents=[risk_acceptance_review_agent],
        tasks=[risk_acceptance_request_task],
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
