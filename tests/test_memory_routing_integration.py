import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import BaseModel

from defectdojo_crewai.memory.context_builder import build_agent_context
from defectdojo_crewai.memory.models import ConversationHistory, WorkflowContext
from defectdojo_crewai.models.schemas import ConversationContext
from defectdojo_crewai.services import routing_service


class _Task(BaseModel):
    description: str
    expected_output: str = "result"
    name: str = "test-task"


class _Crew:
    last_tasks = []
    output = None

    def __init__(self, *, agents, tasks, process, verbose):
        self.agents = agents
        self.tasks = tasks
        _Crew.last_tasks = tasks

    def kickoff(self, inputs):
        return _Crew.output


class _FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.last_prompt = None

    def invoke(self, prompt):
        self.last_prompt = prompt
        return SimpleNamespace(content=self.content)


class MemoryRoutingIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = build_agent_context(
            current_request="triage test 65",
            target_agent="triage",
            business_context=ConversationContext(test_id=65),
            conversation_history=ConversationHistory(),
            workflow_context=WorkflowContext(),
        )

    def test_router_prompt_receives_memory_context(self) -> None:
        fake_llm = _FakeLLM(
            '{"steps": [{"step_id": "step_1", "intent": "triage"}], '
            '"confidence": 0.9}'
        )

        with patch.object(routing_service, "llm", fake_llm):
            plan = routing_service.parse_workflow_plan(
                "triage test 65",
                agent_context=self.context,
            )

        self.assertEqual(plan.steps[0].intent, "triage")
        self.assertIn("[MEMORY_CONTEXT]", fake_llm.last_prompt)
        self.assertIn('"test_id": 65', fake_llm.last_prompt)
        self.assertIn("triage test 65", fake_llm.last_prompt)
        self.assertNotIn("{user_message}", fake_llm.last_prompt)

    def test_run_crew_returns_agent_execution_record(self) -> None:
        _Crew.output = SimpleNamespace(
            raw="agent final output",
            json_dict={"status": "ok"},
            pydantic=None,
            tasks_output=[],
            token_usage={"total_tokens": 17},
        )
        agent = SimpleNamespace(role="Test Agent")
        task = _Task(description="Do the work")

        with patch.object(routing_service, "Crew", _Crew):
            result = routing_service._run_crew(
                agent,
                task,
                {},
                workflow_id="session-1",
                agent_context=self.context,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["agent_execution"]["structured_output"],
            {"status": "ok"},
        )
        self.assertEqual(
            result["agent_execution"]["token_usage"]["total_tokens"],
            17,
        )
        self.assertIn("[MEMORY_CONTEXT]", _Crew.last_tasks[0].description)


if __name__ == "__main__":
    unittest.main()
