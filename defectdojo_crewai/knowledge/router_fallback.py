"""Deterministic audit of low-confidence Router decisions."""

from typing import Any

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.knowledge.evidence import assess_decision_history
from defectdojo_crewai.knowledge.storage import SOURCE_AUDIT
from defectdojo_crewai.models.schemas import WorkflowPlan
from defectdojo_crewai.knowledge.retrieval import (
    KnowledgeSearchOutcome,
    search_knowledge_safely,
)


def annotate_router_fallback(
    plan: WorkflowPlan,
    user_message: str,
) -> WorkflowPlan:
    """Probe decision history and knowledge graph without changing the
    Router's selected steps."""
    if plan.confidence >= settings.router_fallback_confidence_threshold:
        return _with_audit_defaults(plan)

    outcome = search_knowledge_safely(
        query=user_message,
        source_types=[SOURCE_AUDIT],
        k=settings.router_fallback_top_k,
        min_similarity=settings.knowledge_min_similarity,
    )
    plan = _apply_outcome(plan, outcome)

    # ── knowledge-graph injection (deterministic, fast, complements RAG) ─
    # if plan.fallback_used != "decision_history":
    #     plan = _try_kg_injection(plan, user_message)

    return plan


def _try_kg_injection(plan: WorkflowPlan, user_message: str) -> WorkflowPlan:
    """Attempt to enrich the plan with knowledge-graph context.

    If CWE / CVE / OWASP references are found in the user message,
    graph-sourced context blocks are added to *context_injections* and
    *fallback_used* is set to ``"kg_auto_inject"``.
    """
    try:
        from defectdojo_crewai.knowledge.kg import inject_kg_context
    except Exception:
        return plan

    injections = inject_kg_context(user_message)
    if not injections:
        return plan

    return plan.model_copy(
        update={
            "fallback_used": "kg_auto_inject",
            "fallback_similarity": None,
            "fallback_reason": (
                f"Graph injection — {len(injections)} context block(s)"
            ),
            "needs_human_review": False,
            "context_injections": [
                inj.model_dump() for inj in injections
            ],
        }
    )


def _apply_outcome(
    plan: WorkflowPlan,
    outcome: KnowledgeSearchOutcome,
) -> WorkflowPlan:
    if outcome.status == "matched":
        evidence = assess_decision_history(
            outcome.matches,
            min_similarity=settings.router_fallback_min_similarity,
            min_consensus=settings.router_fallback_min_consensus,
        )
        if not evidence.trusted:
            return _needs_human_review(plan, evidence.reason)
        return plan.model_copy(
            update={
                "fallback_used": "decision_history",
                "fallback_similarity": evidence.best_similarity,
                "fallback_reason": evidence.reason,
                "needs_human_review": False,
                "context_injections": _context_references(evidence.matches),
            }
        )
    if outcome.status == "no_match":
        return _needs_human_review(plan, "no_retrieval_match")
    return plan.model_copy(
        update={
            "fallback_used": "qdrant_unavailable",
            "fallback_similarity": None,
            "fallback_reason": "knowledge_service_unavailable",
            "needs_human_review": True,
            "context_injections": [],
        }
    )


def _with_audit_defaults(plan: WorkflowPlan) -> WorkflowPlan:
    return plan.model_copy(
        update={
            "fallback_used": "none",
            "fallback_similarity": None,
            "fallback_reason": None,
            "needs_human_review": False,
            "context_injections": [],
        }
    )


def _needs_human_review(plan: WorkflowPlan, reason: str) -> WorkflowPlan:
    return plan.model_copy(
        update={
            "fallback_used": "no_match",
            "fallback_similarity": None,
            "fallback_reason": reason,
            "needs_human_review": True,
            "context_injections": [],
        }
    )


def _context_references(
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    references = []
    for match in matches:
        metadata = match.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        references.append(
            {
                "source_type": SOURCE_AUDIT,
                "source_id": metadata.get("source_id"),
                "workflow_id": metadata.get("workflow_id"),
                "intent": metadata.get("intent"),
                "score": match.get("score"),
            }
        )
    return references
