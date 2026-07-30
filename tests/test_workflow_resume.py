import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crewai.tools import BaseTool
from pydantic import BaseModel

from defectdojo_crewai.memory.models import ConversationHistory, WorkflowContext
from defectdojo_crewai.models.schemas import (
    ApprovalDecision,
    ConversationContext,
    UserIntent,
    WorkflowPlan,
    WorkflowStep,
    WriteToolCall,
)
from defectdojo_crewai.services import approval_store, routing_service, workflow_store
from defectdojo_crewai.services.approval_service import decide_approval
from defectdojo_crewai.services.tool_policy import (
    register_write_tool,
    request_write_tool_approval,
)
from defectdojo_crewai.services.workflow_store import WorkflowRun


class _ImportResultInput(BaseModel):
    test_id: int


class _ImportResultTool(BaseTool):
    name: str = "test_workflow_import_result_tool"
    description: str = "Return IDs produced by an approved scan import."
    args_schema: type[BaseModel] = _ImportResultInput

    def _run(self, test_id: int) -> dict[str, int]:
        return {
            "test_id": test_id,
            "product_id": 8,
            "engagement_id": 1,
        }


class _FailingImportTool(BaseTool):
    name: str = "test_workflow_failing_import_tool"
    description: str = "Fail an approved scan import."
    args_schema: type[BaseModel] = _ImportResultInput

    def _run(self, test_id: int) -> dict[str, int]:
        raise RuntimeError(f"Import failed for test {test_id}")


class WorkflowResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.approval_patch = patch.object(
            approval_store,
            "DATABASE_PATH",
            root / "approvals.db",
        )
        self.workflow_patch = patch.object(
            workflow_store,
            "DATABASE_PATH",
            root / "workflows.db",
        )
        self.approval_patch.start()
        self.workflow_patch.start()
        approval_store.init_approval_store()
        workflow_store.init_workflow_store()
        register_write_tool(_ImportResultTool())
        register_write_tool(_FailingImportTool())

    def tearDown(self) -> None:
        self.workflow_patch.stop()
        self.approval_patch.stop()
        self.temp_dir.cleanup()

    def test_approval_result_updates_context_and_resumes_remaining_steps(self) -> None:
        workflow_id = "workflow-single"
        approval = self._approval(workflow_id, "step_a", test_id=65)
        self._waiting_run(workflow_id, [approval["approval_id"]])
        executed_steps = []

        def execute(intent, current_workflow_id, step_id, agent_context):
            self.assertEqual(current_workflow_id, workflow_id)
            self.assertEqual(intent.test_id, 65)
            self.assertEqual(agent_context.business_context.test_id, 65)
            executed_steps.append(step_id)
            return {"status": "completed", "output": f"{step_id} complete"}

        with (
            patch.object(routing_service, "_execute_intent", side_effect=execute),
            patch.object(routing_service, "save_session_context"),
            patch.object(routing_service, "append_message"),
            patch.object(routing_service, "enqueue_router_outcome"),
            patch(
                "defectdojo_crewai.services.approval_service.enqueue_approved_execution"
            ),
        ):
            completed = decide_approval(
                ApprovalDecision(
                    approval_id=approval["approval_id"],
                    decision="approve",
                )
            )

        run = workflow_store.get_workflow_run(workflow_id)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(executed_steps, ["step_b", "step_c"])
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.context.test_id, 65)
        self.assertEqual(run.context.product_id, 8)
        self.assertEqual(run.context.engagement_id, 1)
        self.assertEqual(
            run.step_results[0]["result"]["results"][0]["result"]["test_id"],
            65,
        )

        with patch.object(routing_service, "_execute_intent") as execute_again:
            self.assertIsNone(routing_service.resume_workflow(workflow_id))
        execute_again.assert_not_called()

    def test_multiple_approvals_resume_only_after_all_are_completed(self) -> None:
        workflow_id = "workflow-multiple"
        first = self._approval(workflow_id, "step_a", test_id=65)
        second = self._approval(workflow_id, "step_a", test_id=66)
        self._waiting_run(
            workflow_id,
            [first["approval_id"], second["approval_id"]],
        )

        with (
            patch.object(routing_service, "_execute_intent") as execute,
            patch(
                "defectdojo_crewai.services.approval_service.enqueue_approved_execution"
            ),
        ):
            decide_approval(
                ApprovalDecision(
                    approval_id=first["approval_id"],
                    decision="approve",
                )
            )
            self.assertEqual(
                workflow_store.get_workflow_run(workflow_id).status,
                "waiting_approval",
            )
            execute.assert_not_called()

        with (
            patch.object(
                routing_service,
                "_execute_intent",
                return_value={"status": "completed"},
            ) as execute,
            patch.object(routing_service, "save_session_context"),
            patch.object(routing_service, "append_message"),
            patch.object(routing_service, "enqueue_router_outcome"),
            patch(
                "defectdojo_crewai.services.approval_service.enqueue_approved_execution"
            ),
        ):
            decide_approval(
                ApprovalDecision(
                    approval_id=second["approval_id"],
                    decision="approve",
                )
            )

        self.assertEqual(execute.call_count, 2)
        self.assertEqual(
            workflow_store.get_workflow_run(workflow_id).status,
            "completed",
        )

    def test_rejection_stops_workflow_and_cancels_sibling_approvals(self) -> None:
        workflow_id = "workflow-rejected"
        first = self._approval(workflow_id, "step_a", test_id=65)
        second = self._approval(workflow_id, "step_a", test_id=66)
        self._waiting_run(
            workflow_id,
            [first["approval_id"], second["approval_id"]],
        )

        rejected = decide_approval(
            ApprovalDecision(
                approval_id=first["approval_id"],
                decision="reject",
            )
        )

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(
            approval_store.get_approval(second["approval_id"])["status"],
            "cancelled",
        )
        self.assertEqual(
            workflow_store.get_workflow_run(workflow_id).status,
            "rejected",
        )

    def test_write_failure_marks_workflow_failed(self) -> None:
        workflow_id = "workflow-failed"
        approval = request_write_tool_approval(
            [
                WriteToolCall(
                    tool_name=_FailingImportTool().name,
                    arguments={"test_id": 65},
                    requested_by="scan_import_agent",
                )
            ],
            title="Approve failing import",
            description="Approve failing import",
            workflow_id=workflow_id,
            step_id="step_a",
            requested_by="scan_import_agent",
        )
        self._waiting_run(workflow_id, [approval["approval_id"]])

        with self.assertRaisesRegex(RuntimeError, "Import failed"):
            decide_approval(
                ApprovalDecision(
                    approval_id=approval["approval_id"],
                    decision="approve",
                )
            )

        self.assertEqual(
            approval_store.get_approval(approval["approval_id"])["status"],
            "failed",
        )
        self.assertEqual(
            workflow_store.get_workflow_run(workflow_id).status,
            "failed",
        )

    def test_resume_claim_is_atomic(self) -> None:
        workflow_id = "workflow-claim"
        self._waiting_run(workflow_id, ["approval-placeholder"])

        first = workflow_store.claim_workflow_resume(workflow_id)
        second = workflow_store.claim_workflow_resume(workflow_id)

        self.assertIsNotNone(first)
        self.assertEqual(first.status, "resuming")
        self.assertIsNone(second)

    def _approval(
        self,
        workflow_id: str,
        step_id: str,
        *,
        test_id: int,
    ) -> dict:
        return request_write_tool_approval(
            [
                WriteToolCall(
                    tool_name=_ImportResultTool().name,
                    arguments={"test_id": test_id},
                    requested_by="scan_import_agent",
                )
            ],
            title="Approve test import",
            description="Approve test import",
            workflow_id=workflow_id,
            step_id=step_id,
            requested_by="scan_import_agent",
        )

    def _waiting_run(
        self,
        workflow_id: str,
        approval_ids: list[str],
    ) -> WorkflowRun:
        plan = WorkflowPlan(
            steps=[
                WorkflowStep(step_id="step_a", intent="import_scan"),
                WorkflowStep(
                    step_id="step_b",
                    intent="deduplication",
                    depends_on=["step_a"],
                ),
                WorkflowStep(
                    step_id="step_c",
                    intent="triage",
                    depends_on=["step_b"],
                ),
            ]
        )
        waiting_result = {
            "status": "waiting_approval",
            "approval_id": approval_ids[0],
            "approval_ids": approval_ids,
        }
        return workflow_store.create_workflow_run(
            WorkflowRun(
                workflow_id=workflow_id,
                session_id="session-1",
                status="waiting_approval",
                plan=plan,
                current_step_index=0,
                context=ConversationContext(),
                explicit_context=ConversationContext(),
                conversation_history=ConversationHistory(),
                workflow_context=WorkflowContext(),
                step_results=[
                    {
                        "step_id": "step_a",
                        "intent": "import_scan",
                        "depends_on": [],
                        "status": "waiting_approval",
                        "result": waiting_result,
                    }
                ],
                user_message="Import, deduplicate, then triage.",
                representative_intent=UserIntent(
                    intent="import_scan"
                ).model_dump(mode="json"),
            )
        )


if __name__ == "__main__":
    unittest.main()
