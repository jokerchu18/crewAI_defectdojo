"""In-process progress registry used to visualize workflow execution.

Progress is intentionally kept in process memory: it only needs to live for
the duration of one /api/chat call and is polled by the same web process.
"""

from copy import deepcopy
from threading import Lock
from time import time
from typing import Any

_LOCK = Lock()
_PROGRESS: dict[str, dict[str, Any]] = {}
_MAX_ENTRIES = 500


def begin_progress(session_id: str, message: str = "正在解析请求并规划工作流...") -> None:
    now = time()
    with _LOCK:
        _PROGRESS[session_id] = {
            "phase": "planning",
            "status": "running",
            "message": message,
            "steps": [],
            "started_at": now,
            "updated_at": now,
        }
        _prune()


def set_progress_steps(session_id: str, steps: list[dict[str, Any]]) -> None:
    with _LOCK:
        entry = _PROGRESS.get(session_id)
        if entry is None:
            return
        entry["phase"] = "running"
        entry["message"] = "工作流已规划，开始执行步骤。"
        entry["steps"] = deepcopy(steps)
        entry["updated_at"] = time()


def update_progress_step(session_id: str, step_id: str, status: str) -> None:
    with _LOCK:
        entry = _PROGRESS.get(session_id)
        if entry is None:
            return
        for step in entry["steps"]:
            if step.get("step_id") == step_id:
                step["status"] = status
                break
        if status == "running":
            entry["message"] = f"正在执行步骤 {step_id}..."
        entry["updated_at"] = time()


def finish_progress(session_id: str, status: str, message: str = "") -> None:
    with _LOCK:
        entry = _PROGRESS.get(session_id)
        if entry is None:
            return
        entry["phase"] = "done"
        entry["status"] = status
        if message:
            entry["message"] = message
        entry["updated_at"] = time()


def get_progress(session_id: str) -> dict[str, Any] | None:
    with _LOCK:
        entry = _PROGRESS.get(session_id)
        return deepcopy(entry) if entry is not None else None


def _prune() -> None:
    if len(_PROGRESS) <= _MAX_ENTRIES:
        return
    oldest = sorted(_PROGRESS.items(), key=lambda item: item[1]["updated_at"])
    for session_id, _ in oldest[: len(_PROGRESS) - _MAX_ENTRIES]:
        _PROGRESS.pop(session_id, None)
