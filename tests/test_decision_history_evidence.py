import unittest

from defectdojo_crewai.knowledge.evidence import assess_decision_history


def _match(intent: str, score: float, outcome: str = "completed") -> dict:
    return {
        "content": "historical workflow",
        "score": score,
        "metadata": {
            "source_type": "audit",
            "intent": intent,
            "outcome": outcome,
            "verification_status": "observed",
        },
    }


class DecisionHistoryEvidenceTests(unittest.TestCase):
    def test_accepts_two_completed_high_similarity_matches_for_one_intent(self):
        result = assess_decision_history(
            [_match("triage", 0.91), _match("triage", 0.83)],
            min_similarity=0.75,
            min_consensus=2,
        )

        self.assertTrue(result.trusted)
        self.assertEqual(result.intent, "triage")
        self.assertEqual(result.best_similarity, 0.91)
        self.assertEqual(result.reason, "trusted_consensus")

    def test_rejects_a_single_match(self):
        result = assess_decision_history(
            [_match("triage", 0.91)],
            min_similarity=0.75,
            min_consensus=2,
        )

        self.assertFalse(result.trusted)
        self.assertEqual(result.reason, "insufficient_consensus")

    def test_rejects_low_similarity_or_unsuccessful_history(self):
        result = assess_decision_history(
            [
                _match("triage", 0.74),
                _match("triage", 0.91, outcome="failed"),
            ],
            min_similarity=0.75,
            min_consensus=2,
        )

        self.assertFalse(result.trusted)
        self.assertEqual(result.reason, "no_eligible_history")

    def test_rejects_tied_intent_consensus(self):
        result = assess_decision_history(
            [
                _match("triage", 0.91),
                _match("remediation", 0.90),
            ],
            min_similarity=0.75,
            min_consensus=1,
        )

        self.assertFalse(result.trusted)
        self.assertEqual(result.reason, "conflicting_consensus")


if __name__ == "__main__":
    unittest.main()
