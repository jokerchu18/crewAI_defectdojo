import unittest

from pydantic import ValidationError

from defectdojo_crewai.models.schemas import WorkflowPlan


class WorkflowPlanFallbackSchemaTests(unittest.TestCase):
    def test_defaults_preserve_existing_router_output(self) -> None:
        plan = WorkflowPlan.model_validate(
            {
                "steps": [],
                "message": "No matching action",
                "confidence": 0.6,
            }
        )

        self.assertEqual(plan.fallback_used, "none")
        self.assertIsNone(plan.fallback_similarity)
        self.assertIsNone(plan.fallback_reason)
        self.assertFalse(plan.needs_human_review)
        self.assertEqual(plan.context_injections, [])

    def test_fallback_audit_fields_are_serialized(self) -> None:
        plan = WorkflowPlan(
            fallback_used="decision_history",
            fallback_similarity=0.86,
            fallback_reason="trusted_consensus",
            context_injections=[
                {"source_type": "audit", "source_id": "workflow-123"}
            ],
        )

        payload = plan.model_dump()
        self.assertEqual(payload["fallback_used"], "decision_history")
        self.assertEqual(payload["fallback_similarity"], 0.86)
        self.assertEqual(payload["fallback_reason"], "trusted_consensus")
        self.assertFalse(payload["needs_human_review"])
        self.assertEqual(
            payload["context_injections"],
            [{"source_type": "audit", "source_id": "workflow-123"}],
        )

    def test_similarity_must_be_between_zero_and_one(self) -> None:
        with self.assertRaises(ValidationError):
            WorkflowPlan(fallback_similarity=1.1)

    def test_unknown_fallback_type_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            WorkflowPlan(fallback_used="untracked_source")


if __name__ == "__main__":
    unittest.main()
