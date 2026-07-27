import unittest
from unittest.mock import patch

from defectdojo_crewai.knowledge import retrieval


class KnowledgeSearchOutcomeTests(unittest.TestCase):
    def test_returns_matched_with_best_similarity(self) -> None:
        matches = [
            {"content": "first", "metadata": {}, "score": 0.72},
            {"content": "second", "metadata": {}, "score": 0.91},
        ]

        with patch.object(
            retrieval,
            "search_knowledge",
            return_value=matches,
        ):
            outcome = retrieval.search_knowledge_safely(query="triage")

        self.assertEqual(outcome.status, "matched")
        self.assertEqual(outcome.matches, matches)
        self.assertEqual(outcome.best_similarity, 0.91)
        self.assertIsNone(outcome.error_type)

    def test_returns_no_match_for_empty_results(self) -> None:
        with patch.object(
            retrieval,
            "search_knowledge",
            return_value=[],
        ):
            outcome = retrieval.search_knowledge_safely(query="unknown")

        self.assertEqual(outcome.status, "no_match")
        self.assertEqual(outcome.matches, [])
        self.assertIsNone(outcome.best_similarity)
        self.assertIsNone(outcome.error_type)

    def test_returns_unavailable_when_retrieval_fails(self) -> None:
        with patch.object(
            retrieval,
            "search_knowledge",
            side_effect=ConnectionError("qdrant is offline"),
        ), self.assertLogs(retrieval.LOGGER, level="ERROR") as logs:
            outcome = retrieval.search_knowledge_safely(
                query="triage"
            )

        self.assertEqual(outcome.status, "unavailable")
        self.assertEqual(outcome.matches, [])
        self.assertIsNone(outcome.best_similarity)
        self.assertEqual(outcome.error_type, "ConnectionError")
        self.assertIn("Knowledge retrieval is unavailable", logs.output[0])

    def test_returns_unavailable_when_knowledge_is_disabled(self) -> None:
        with patch.object(
            retrieval.settings,
            "knowledge_enabled",
            False,
        ), patch.object(retrieval, "search_knowledge") as search:
            outcome = retrieval.search_knowledge_safely(query="triage")

        search.assert_not_called()
        self.assertEqual(outcome.status, "unavailable")
        self.assertEqual(outcome.error_type, "KnowledgeDisabled")


if __name__ == "__main__":
    unittest.main()
