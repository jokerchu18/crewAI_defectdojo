import unittest
from unittest.mock import patch

from defectdojo_crewai.memory.conversation_summarizer import (
    summarize_incrementally,
)
from defectdojo_crewai.memory.token_counter import count_text_tokens


class _SummaryLLM:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error

    def call(self, messages):
        if self.error is not None:
            raise self.error
        return self.response


class ConversationSummarizerTests(unittest.TestCase):
    def test_llm_summary_is_limited_to_summary_budget(self) -> None:
        module = __import__(
            "defectdojo_crewai.memory.conversation_summarizer",
            fromlist=["settings"],
        )
        with (
            patch.object(module.settings, "context_summary_token_budget", 20),
            patch.object(
                module.settings,
                "context_summary_input_token_budget",
                100,
            ),
            patch.object(
                module.llm_config,
                "getLLM",
                return_value=_SummaryLLM("summary " * 100),
            ) as get_llm,
        ):
            summary = summarize_incrementally(
                "",
                [{"id": 1, "role": "user", "content": "old requirement"}],
            )

        self.assertLessEqual(count_text_tokens(summary), 20)
        self.assertEqual(get_llm.call_args.kwargs["max_tokens"], 20)

    def test_llm_failure_uses_bounded_deterministic_fallback(self) -> None:
        module = __import__(
            "defectdojo_crewai.memory.conversation_summarizer",
            fromlist=["settings"],
        )
        with (
            patch.object(module.settings, "context_summary_token_budget", 30),
            patch.object(
                module.settings,
                "context_summary_input_token_budget",
                100,
            ),
            patch.object(
                module.llm_config,
                "getLLM",
                return_value=_SummaryLLM(error=RuntimeError("LLM unavailable")),
            ),
        ):
            summary = summarize_incrementally(
                "existing decision",
                [{"id": 2, "role": "user", "content": "new constraint"}],
            )

        self.assertIn("existing decision", summary)
        self.assertIn("new constraint", summary)
        self.assertLessEqual(count_text_tokens(summary), 30)


if __name__ == "__main__":
    unittest.main()
