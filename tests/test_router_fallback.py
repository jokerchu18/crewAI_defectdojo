import unittest
from unittest.mock import patch

from defectdojo_crewai.models.schemas import WorkflowPlan, WorkflowStep
from defectdojo_crewai.knowledge import router_fallback
from defectdojo_crewai.knowledge.retrieval import KnowledgeSearchOutcome


def _plan(confidence: float) -> WorkflowPlan:
    return WorkflowPlan(
        steps=[WorkflowStep(step_id="step_1", intent="triage")],
        confidence=confidence,
    )


class RouterFallbackTests(unittest.TestCase):
    def test_high_confidence_skips_knowledge_search(self) -> None:
        plan = _plan(0.9).model_copy(
            update={
                "fallback_used": "qdrant_unavailable",
                "needs_human_review": True,
            }
        )

        with patch.object(
            router_fallback,
            "search_knowledge_safely",
        ) as search:
            result = router_fallback.annotate_router_fallback(plan, "triage")

        search.assert_not_called()
        self.assertEqual(result.fallback_used, "none")
        self.assertFalse(result.needs_human_review)
        self.assertEqual(result.steps, plan.steps)

    def test_low_confidence_records_decision_history_match(self) -> None:
        outcome = KnowledgeSearchOutcome(status="matched", matches=[
            {
                "content": "historical routing",
                "score": 0.88,
                "metadata": {
                    "source_type": "audit",
                    "source_id": "session-123",
                    "workflow_id": "session-123",
                    "intent": "triage",
                    "outcome": "completed",
                    "verification_status": "observed",
                },
            },
            {
                "content": "another historical routing",
                "score": 0.82,
                "metadata": {
                    "source_type": "audit",
                    "source_id": "session-456",
                    "workflow_id": "session-456",
                    "intent": "triage",
                    "outcome": "completed",
                    "verification_status": "observed",
                },
            },
        ])

        with patch.object(
            router_fallback,
            "search_knowledge_safely",
            return_value=outcome,
        ):
            result = router_fallback.annotate_router_fallback(
                _plan(0.5),
                "handle this finding",
            )

        self.assertEqual(result.fallback_used, "decision_history")
        self.assertEqual(result.fallback_similarity, 0.88)
        self.assertEqual(result.fallback_reason, "trusted_consensus")
        self.assertFalse(result.needs_human_review)
        self.assertEqual(result.steps[0].intent, "triage")
        self.assertEqual(
            result.context_injections[0]["source_id"],
            "session-123",
        )

    def test_low_confidence_no_match_marks_human_review(self) -> None:
        with patch.object(
            router_fallback,
            "search_knowledge_safely",
            return_value=KnowledgeSearchOutcome(status="no_match"),
        ):
            result = router_fallback.annotate_router_fallback(
                _plan(0.5),
                "ambiguous request",
            )

        self.assertEqual(result.fallback_used, "no_match")
        self.assertEqual(result.fallback_reason, "no_retrieval_match")
        self.assertTrue(result.needs_human_review)

    def test_low_confidence_unavailable_marks_human_review(self) -> None:
        with patch.object(
            router_fallback,
            "search_knowledge_safely",
            return_value=KnowledgeSearchOutcome(
                status="unavailable",
                error_type="ConnectionError",
            ),
        ):
            result = router_fallback.annotate_router_fallback(
                _plan(0.5),
                "ambiguous request",
            )

        self.assertEqual(result.fallback_used, "qdrant_unavailable")
        self.assertEqual(
            result.fallback_reason,
            "knowledge_service_unavailable",
        )
        self.assertTrue(result.needs_human_review)


if __name__ == "__main__":
    unittest.main()
