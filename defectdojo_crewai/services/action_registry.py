from collections.abc import Callable
from typing import Any


ActionExecutor = Callable[[dict[str, Any]], dict[str, Any]]
_ACTION_EXECUTORS: dict[str, ActionExecutor] = {}


def register_action(action_type: str):
    def decorator(executor: ActionExecutor):
        _ACTION_EXECUTORS[action_type] = executor
        return executor

    return decorator


def execute_action(action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    executor = _ACTION_EXECUTORS.get(action_type)
    if executor is None:
        raise ValueError(f"Unsupported approval action: {action_type}")
    return executor(payload)
