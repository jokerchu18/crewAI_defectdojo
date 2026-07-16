import json
from uuid import uuid4

from defectdojo_crewai.models.schemas import ApprovalDecision, ChatRequest
from defectdojo_crewai.services.approval_service import (
    decide_approval,
    pending_approvals,
)
from defectdojo_crewai.services.approval_store import init_approval_store
from defectdojo_crewai.services.routing_service import handle_chat_request
from defectdojo_crewai.services.session_service import get_session_context

# chat入口文件

def run_chat() -> None:
    init_approval_store()
    session_id = str(uuid4())
    _print_help()

    while True:
        try:
            message = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return

        if not message:
            continue
        if message.lower() in {"exit", "quit", "退出"}:
            return
        if message.lower() in {"help", "帮助"}:
            _print_help()
            continue
        if message.lower() in {"approvals", "审批列表"}:
            _print_pending_approvals()
            continue
        if message.lower() in {"context", "上下文"}:
            _print_json(get_session_context(session_id).model_dump())
            continue
        if message.lower().startswith("approve "):
            _approve(message)
            continue
        if message.lower().startswith("reject "):
            _reject(message)
            continue

        try:
            result = handle_chat_request(
                ChatRequest(
                    session_id=session_id,
                    message=message,
                )
            )
            _print_json(result.model_dump())
        except Exception as exc:
            print(f"请求执行失败: {exc}")


def _approve(message: str) -> None:
    parts = message.split(maxsplit=2)
    if len(parts) < 2:
        print("用法: approve <approval_id> [finding_id1,finding_id2]")
        return

    approved_finding_ids: list[int] = []
    if len(parts) == 3 and parts[2].strip():
        try:
            approved_finding_ids = [
                int(value.strip())
                for value in parts[2].split(",")
                if value.strip()
            ]
        except ValueError:
            print("finding_id 必须是整数，并使用逗号分隔。")
            return

    try:
        result = decide_approval(
            ApprovalDecision(
                approval_id=parts[1],
                decision="approve",
                approved_finding_ids=approved_finding_ids,
            )
        )
        _print_json(result)
    except Exception as exc:
        print(f"审批执行失败: {exc}")


def _reject(message: str) -> None:
    parts = message.split(maxsplit=1)
    if len(parts) < 2:
        print("用法: reject <approval_id>")
        return

    try:
        result = decide_approval(
            ApprovalDecision(
                approval_id=parts[1],
                decision="reject",
            )
        )
        _print_json(result)
    except Exception as exc:
        print(f"拒绝审批失败: {exc}")


def _print_pending_approvals() -> None:
    approvals = pending_approvals()
    if not approvals:
        print("当前没有待审批操作。")
        return

    for approval in approvals:
        print("\n" + "=" * 72)
        print(f"approval_id: {approval['approval_id']}")
        print(f"title: {approval['title']}")
        print(f"action_type: {approval['action_type']}")
        print(f"risk_level: {approval['risk_level']}")
        candidates = approval["payload"].get("approved_candidates", [])
        for candidate in candidates:
            print(
                f"- finding_id={candidate['finding_id']} "
                f"severity={candidate['severity']} "
                f"title={candidate['title']}"
            )


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _print_help() -> None:
    print(
        "\n可用命令：\n"
        "  直接输入自然语言，例如：评估 Product 1 的 Medium 漏洞风险接受\n"
        "  approvals                         查看待审批操作\n"
        "  context                           查看当前会话保存的 ID\n"
        "  approve <approval_id>             批准该审批中的全部 findings\n"
        "  approve <approval_id> 32,35       只批准指定 findings\n"
        "  reject <approval_id>              拒绝审批\n"
        "  help                              查看帮助\n"
        "  exit                              退出\n"
    )
