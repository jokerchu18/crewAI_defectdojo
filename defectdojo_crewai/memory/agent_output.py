from typing import Any

from pydantic import BaseModel

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.memory.models import AgentExecutionRecord


def capture_agent_execution(output: Any, agent: Any, task: Any) -> AgentExecutionRecord:
    raw_output = getattr(output, "raw", None)
    if raw_output is None:
        raw_output = str(output)

    structured_output = getattr(output, "json_dict", None)
    if structured_output is None:
        structured_output = _serialize(getattr(output, "pydantic", None))

    task_outputs = []
    for task_output in getattr(output, "tasks_output", None) or []:
        task_outputs.append(
            {
                "raw": _truncate(str(getattr(task_output, "raw", "") or "")),
                "json": _serialize(getattr(task_output, "json_dict", None)),
                "pydantic": _serialize(getattr(task_output, "pydantic", None)),
            }
        )

    return AgentExecutionRecord(
        agent=str(getattr(agent, "role", None) or type(agent).__name__),
        task=str(getattr(task, "name", None) or getattr(task, "expected_output", "")),
        raw_output=_truncate(str(raw_output)),
        structured_output=_serialize(structured_output),
        task_outputs=task_outputs,
        token_usage=_serialize(getattr(output, "token_usage", None)) or {},
    )


def _serialize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def _truncate(value: str) -> str:
    return value[: settings.context_agent_output_max_chars]
