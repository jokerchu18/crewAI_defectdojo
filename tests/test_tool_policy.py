import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from defectdojo_crewai.models.schemas import ApprovalDecision, WriteToolCall
from defectdojo_crewai.services import approval_store
from defectdojo_crewai.services.approval_service import (
    _filter_approved_findings,
    decide_approval,
)
from defectdojo_crewai.services.risk_acceptance_actions import (
    build_risk_acceptance_tool_calls,
)
from defectdojo_crewai.services.tool_policy import (
    capture_write_approvals,
    execute_write_tool_calls,
    gated_write_tool,
    request_write_tool_approval,
)
from defectdojo_crewai.tools import defectdojo_api
from defectdojo_crewai.utils.retry import with_timeout


class _DummyWriteInput(BaseModel):
    value: int = Field(...)


_DUMMY_WRITES: list[int] = []


class _DummyWriteTool(BaseTool):
    name: str = "test_dummy_write_tool"
    description: str = "Record one test value."
    args_schema: type[BaseModel] = _DummyWriteInput

    def _run(self, value: int) -> dict[str, int]:
        _DUMMY_WRITES.append(value)
        return {"written": value}


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self.payload


class ToolPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "approvals.db"
        self.database_patch = patch.object(
            approval_store,
            "DATABASE_PATH",
            database_path,
        )
        self.database_patch.start()
        approval_store.init_approval_store()
        _DUMMY_WRITES.clear()

    def tearDown(self) -> None:
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def test_gated_tool_executes_only_after_approval(self) -> None:
        tool = gated_write_tool(
            _DummyWriteTool(),
            requested_by="test_agent",
        )

        pending = tool.run(value=7)

        self.assertEqual(pending["status"], "waiting_approval")
        self.assertEqual(_DUMMY_WRITES, [])

        completed = decide_approval(
            ApprovalDecision(
                approval_id=pending["approval_id"],
                decision="approve",
            )
        )

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(_DUMMY_WRITES, [7])
        self.assertEqual(
            completed["result"]["results"][0]["result"],
            {"written": 7},
        )

    def test_capture_collects_agent_write_approvals(self) -> None:
        tool = gated_write_tool(
            _DummyWriteTool(),
            requested_by="test_agent",
        )

        with capture_write_approvals(workflow_id="session-123") as approvals:
            tool.run(value=11)

        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["action_type"], "tool.execute")
        self.assertEqual(approvals[0]["workflow_id"], "session-123")
        self.assertEqual(
            approvals[0]["payload"]["tool_calls"][0]["arguments"],
            {"value": 11},
        )

    def test_capture_context_is_preserved_in_timeout_worker(self) -> None:
        tool = gated_write_tool(
            _DummyWriteTool(),
            requested_by="test_agent",
        )

        with capture_write_approvals(
            workflow_id="workflow-123",
            step_id="step-import",
        ) as approvals:
            pending = with_timeout(1, tool.run, value=13)

        self.assertEqual(pending["status"], "waiting_approval")
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["workflow_id"], "workflow-123")
        self.assertEqual(approvals[0]["step_id"], "step-import")

    def test_import_scan_error_includes_defectdojo_response(self) -> None:
        report_path = Path(self.temp_dir.name) / "scan.nessus"
        report_path.write_text("<NessusClientData_v2 />", encoding="utf-8")
        request = httpx.Request(
            "POST",
            "http://dojo.test/api/v2/import-scan/",
        )
        response = httpx.Response(
            400,
            request=request,
            json={"scan_type": ["Unsupported scan type."]},
        )

        with patch.object(
            defectdojo_api.httpx,
            "post",
            return_value=response,
        ):
            with self.assertRaisesRegex(
                httpx.HTTPStatusError,
                "Unsupported scan type",
            ):
                defectdojo_api.defectdojo_import_scan_tool(
                    base_url="http://dojo.test",
                    api_key="test-token",
                    scan_type="Nessus Scan",
                    engagement_id=1,
                    scan_file_path=str(report_path),
                )

    def test_import_scan_uses_tenable_parser_alias(self) -> None:
        report_path = Path(self.temp_dir.name) / "scan.nessus"
        report_path.write_text("<NessusClientData_v2 />", encoding="utf-8")
        response = httpx.Response(
            200,
            request=httpx.Request(
                "POST",
                "http://dojo.test/api/v2/import-scan/",
            ),
            json={
                "test_id": 10,
                "engagement_id": 1,
                "product_id": 8,
            },
        )

        with patch.object(
            defectdojo_api.httpx,
            "post",
            return_value=response,
        ) as post:
            defectdojo_api.defectdojo_import_scan_tool(
                base_url="http://dojo.test",
                api_key="test-token",
                scan_type="Nessus Scan",
                engagement_id=1,
                scan_file_path=str(report_path),
            )

        self.assertEqual(post.call_args.kwargs["data"]["scan_type"], "Tenable Scan")

    def test_edited_approval_cannot_replace_tool_identity(self) -> None:
        tool_call = WriteToolCall(
            tool_name="test_dummy_write_tool",
            arguments={"value": 3},
            requested_by="test_agent",
        )
        approval = request_write_tool_approval(
            [tool_call],
            title="Test approval",
            description="Test approval",
            requested_by="test_agent",
        )
        edited_payload = dict(approval["payload"])
        edited_payload["tool_calls"] = [
            {
                **approval["payload"]["tool_calls"][0],
                "tool_name": "another_write_tool",
            }
        ]

        with self.assertRaisesRegex(ValueError, "preserve tool call IDs"):
            decide_approval(
                ApprovalDecision(
                    approval_id=approval["approval_id"],
                    decision="approve",
                    edited_payload=edited_payload,
                )
            )

    def test_unknown_write_tool_is_rejected_before_approval(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unregistered write tool"):
            request_write_tool_approval(
                [
                    WriteToolCall(
                        tool_name="unknown_write_tool",
                        arguments={},
                        requested_by="test_agent",
                    )
                ],
                title="Invalid approval",
                description="Invalid approval",
                requested_by="test_agent",
            )

    def test_edited_approval_cannot_duplicate_tool_call(self) -> None:
        tool_call = WriteToolCall(
            tool_name="test_dummy_write_tool",
            arguments={"value": 4},
            requested_by="test_agent",
        )
        approval = request_write_tool_approval(
            [tool_call],
            title="Test approval",
            description="Test approval",
            requested_by="test_agent",
        )
        original_call = approval["payload"]["tool_calls"][0]
        edited_payload = {
            **approval["payload"],
            "tool_calls": [original_call, original_call],
        }

        with self.assertRaisesRegex(ValueError, "preserve tool call IDs"):
            decide_approval(
                ApprovalDecision(
                    approval_id=approval["approval_id"],
                    decision="approve",
                    edited_payload=edited_payload,
                )
            )

    def test_risk_acceptance_selection_keeps_both_calls(self) -> None:
        candidates: list[dict[str, Any]] = [
            _risk_candidate(10),
            _risk_candidate(20),
        ]
        tool_calls = build_risk_acceptance_tool_calls(
            candidates,
            requested_by="risk_acceptance_review_agent",
        )
        payload = {
            "approved_candidates": candidates,
            "tool_calls": [
                tool_call.model_dump() for tool_call in tool_calls
            ],
        }

        filtered = _filter_approved_findings(payload, [20])

        self.assertEqual(
            [
                item["finding_id"]
                for item in filtered["approved_candidates"]
            ],
            [20],
        )
        self.assertEqual(len(filtered["tool_calls"]), 2)
        self.assertEqual(
            {
                item["arguments"]["finding_id"]
                for item in filtered["tool_calls"]
            },
            {20},
        )

    def test_risk_acceptance_executes_tools_without_agent(self) -> None:
        tool_calls = build_risk_acceptance_tool_calls(
            [_risk_candidate(30)],
            requested_by="risk_acceptance_review_agent",
        )

        with (
            patch.object(
                defectdojo_api.httpx,
                "post",
                return_value=_Response({"id": 101}),
            ) as post,
            patch.object(
                defectdojo_api.httpx,
                "patch",
                return_value=_Response({"id": 30, "risk_accepted": True}),
            ) as update,
        ):
            result = execute_write_tool_calls(
                {
                    "tool_calls": [
                        tool_call.model_dump() for tool_call in tool_calls
                    ]
                }
            )

        self.assertEqual(
            [item["tool_name"] for item in result["results"]],
            [
                "defectdojo_create_approved_risk_acceptance_tool",
                "defectdojo_update_risk_acceptance_tool",
            ],
        )
        post.assert_called_once()
        update.assert_called_once()


def _risk_candidate(finding_id: int) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "severity": "Low",
        "title": f"Finding {finding_id}",
        "decision": "Accept",
        "reason": "Accepted for testing",
        "expiration_date": "2026-12-31",
        "reactivate_expired": True,
        "restart_sla_expired": False,
    }


if __name__ == "__main__":
    unittest.main()
