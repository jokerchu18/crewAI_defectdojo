import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import BaseModel

from defectdojo_crewai.memory.agent_output import capture_agent_execution
from defectdojo_crewai.memory.context_builder import (
    append_workflow_result,
    build_agent_context,
    load_memory_snapshot,
    prepare_task_with_context,
    render_agent_context,
)
from defectdojo_crewai.memory.models import ConversationHistory, WorkflowContext
from defectdojo_crewai.models.schemas import ConversationContext


class _Task(BaseModel):
    description: str


class MemoryContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.summary_patch = patch(
            "defectdojo_crewai.memory.context_builder.get_conversation_summary",
            return_value=None,
        )
        self.summary_patch.start()

    def tearDown(self) -> None:
        self.summary_patch.stop()

    def test_loads_history_without_duplicating_current_request(self) -> None:
        messages = [
            {"role": "user", "content": "first request", "created_at": 1.0},
            {"role": "assistant", "content": "first answer", "created_at": 2.0},
            {"role": "user", "content": "current request", "created_at": 3.0},
        ]

        with patch(
            "defectdojo_crewai.memory.context_builder.get_messages",
            return_value=messages,
        ):
            snapshot = load_memory_snapshot("session-1", "current request")

        contents = [
            message.content
            for message in snapshot.conversation_history.recent_messages
        ]
        self.assertEqual(contents, ["first request", "first answer"])

    def test_restores_historical_agent_result_as_workflow_memory(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "workflow completed",
                "result": {
                    "steps": [
                        {
                            "step_id": "step_1",
                            "intent": "import_scan",
                            "status": "completed",
                            "result": {
                                "status": "completed",
                                "output": {
                                    "test_id": 65,
                                    "product_id": 8,
                                },
                            },
                        }
                    ]
                },
            }
        ]

        with patch(
            "defectdojo_crewai.memory.context_builder.get_messages",
            return_value=messages,
        ):
            snapshot = load_memory_snapshot("session-1", "next request")

        step = snapshot.workflow_context.steps[0]
        self.assertEqual(step.source, "history")
        self.assertEqual(step.facts["test_id"], 65)
        self.assertEqual(step.facts["product_id"], 8)

    def test_history_overflow_updates_incremental_summary(self) -> None:
        messages = [
            {
                "id": 1,
                "role": "user",
                "content": "old question " * 20,
                "created_at": 1.0,
            },
            {
                "id": 2,
                "role": "assistant",
                "content": "old answer " * 20,
                "created_at": 2.0,
            },
            {
                "id": 3,
                "role": "user",
                "content": "recent question",
                "created_at": 3.0,
            },
            {
                "id": 4,
                "role": "assistant",
                "content": "recent answer",
                "created_at": 4.0,
            },
            {
                "id": 5,
                "role": "user",
                "content": "current request",
                "created_at": 5.0,
            },
        ]
        module = __import__(
            "defectdojo_crewai.memory.context_builder",
            fromlist=["settings"],
        )

        with (
            patch(
                "defectdojo_crewai.memory.context_builder.get_messages",
                return_value=messages,
            ),
            patch(
                "defectdojo_crewai.memory.context_builder.summarize_incrementally",
                return_value="updated summary",
            ) as summarize,
            patch(
                "defectdojo_crewai.memory.context_builder.save_conversation_summary"
            ) as save,
            patch.object(module.settings, "context_history_token_budget", 30),
        ):
            snapshot = load_memory_snapshot("session-1", "current request")

        self.assertEqual(snapshot.conversation_history.summary, "updated summary")
        self.assertEqual(
            [
                message.message_id
                for message in snapshot.conversation_history.recent_messages
            ],
            [3, 4],
        )
        summarized_messages = summarize.call_args.args[1]
        self.assertEqual(
            [message["id"] for message in summarized_messages],
            [1, 2],
        )
        self.assertEqual(
            save.call_args.kwargs["covered_through_message_id"],
            2,
        )

    def test_summary_cursor_prevents_resummarizing_covered_messages(self) -> None:
        messages = [
            {"id": 1, "role": "user", "content": "covered"},
            {"id": 2, "role": "assistant", "content": "covered answer"},
            {"id": 3, "role": "user", "content": "recent"},
            {"id": 4, "role": "assistant", "content": "recent answer"},
        ]
        with (
            patch(
                "defectdojo_crewai.memory.context_builder.get_messages",
                return_value=messages,
            ),
            patch(
                "defectdojo_crewai.memory.context_builder.get_conversation_summary",
                return_value={
                    "summary": "persisted summary",
                    "covered_through_message_id": 2,
                    "source_message_count": 2,
                },
            ),
            patch(
                "defectdojo_crewai.memory.context_builder.summarize_incrementally"
            ) as summarize,
        ):
            snapshot = load_memory_snapshot("session-1", "next request")

        self.assertEqual(snapshot.conversation_history.summary, "persisted summary")
        self.assertEqual(
            [
                message.message_id
                for message in snapshot.conversation_history.recent_messages
            ],
            [3, 4],
        )
        summarize.assert_not_called()

    def test_prompt_contains_all_three_memory_layers(self) -> None:
        workflow = WorkflowContext()
        append_workflow_result(
            workflow,
            workflow_id="session-1",
            step={
                "step_id": "step_1",
                "intent": "import_scan",
                "status": "completed",
                "result": {
                    "status": "completed",
                    "output": {"test_id": 65, "product_id": 8},
                },
            },
        )
        context = build_agent_context(
            current_request="triage imported findings",
            target_agent="triage",
            business_context=ConversationContext(
                test_id=65,
                product_id=8,
            ),
            conversation_history=ConversationHistory(
                summary="The user imported a SARIF report."
            ),
            workflow_context=workflow,
        )

        prompt = render_agent_context(context)

        self.assertIn("[STRUCTURED_FACTS]", prompt)
        self.assertIn('"test_id": 65', prompt)
        self.assertIn("[WORKFLOW_MEMORY]", prompt)
        self.assertIn('"intent": "import_scan"', prompt)
        self.assertIn("[CONVERSATION_MEMORY]", prompt)
        self.assertIn("The user imported a SARIF report.", prompt)

    def test_prompt_respects_context_budget(self) -> None:
        context = build_agent_context(
            current_request="current",
            target_agent="router",
            business_context=ConversationContext(test_id=65),
            conversation_history=ConversationHistory(summary="x" * 1000),
            workflow_context=WorkflowContext(),
        )

        with patch.object(
            __import__(
                "defectdojo_crewai.memory.context_builder",
                fromlist=["settings"],
            ).settings,
            "context_max_chars",
            500,
        ):
            prompt = render_agent_context(context)

        self.assertLessEqual(len(prompt), 500)
        self.assertIn('"test_id": 65', prompt)

    def test_task_copy_receives_context_without_mutating_original(self) -> None:
        task = _Task(description="Original task")
        context = build_agent_context(
            current_request="triage",
            target_agent="triage",
            business_context=ConversationContext(test_id=65),
            conversation_history=ConversationHistory(),
            workflow_context=WorkflowContext(),
        )

        prepared = prepare_task_with_context(task, context)

        self.assertEqual(task.description, "Original task")
        self.assertIn("[MEMORY_CONTEXT]", prepared.description)
        self.assertIn('"test_id": 65', prepared.description)

    def test_agent_execution_is_structured_and_truncated(self) -> None:
        output = SimpleNamespace(
            raw="raw result",
            json_dict={"finding_ids": [1, 2]},
            pydantic=None,
            tasks_output=[],
            token_usage={"total_tokens": 42},
        )
        agent = SimpleNamespace(role="Triage Agent")
        task = SimpleNamespace(name="triage", expected_output="result")

        record = capture_agent_execution(output, agent, task)

        self.assertEqual(record.agent, "Triage Agent")
        self.assertEqual(record.task, "triage")
        self.assertEqual(record.structured_output, {"finding_ids": [1, 2]})
        self.assertEqual(record.token_usage["total_tokens"], 42)


if __name__ == "__main__":
    unittest.main()
