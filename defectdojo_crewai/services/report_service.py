"""Flow-level vulnerability analysis report generation.

Called deterministically after a workflow completes — never decided by an
agent.  Failures are logged and must not affect the workflow result.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from crewai import Crew, Process

from defectdojo_crewai.agents.report import report_agent
from defectdojo_crewai.config.settings import BASE_DIR, settings
from defectdojo_crewai.tasks.report_tasks import report_task
from defectdojo_crewai.utils.retry import execute_agent_with_timeout
from defectdojo_crewai.utils.timeout_configs import AGENT_TIMEOUTS

LOGGER = logging.getLogger(__name__)

REPORTS_DIR = BASE_DIR / "data" / "reports"

_MAX_FIELD_CHARS = 4000
_SKIP_INTENTS = {"unknown"}


def generate_workflow_report(
    *,
    workflow_id: str,
    user_message: str,
    workflow_status: str,
    step_results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Run the report agent over the workflow's step results and write the
    Markdown report to ``data/reports/``.  Returns ``{"report_path": ...}``
    or ``None`` when skipped / failed."""
    try:
        if not _should_generate(step_results):
            return None

        crew = Crew(
            agents=[report_agent],
            tasks=[report_task],
            process=Process.sequential,
            verbose=settings.crew_verbose,
        )
        output = execute_agent_with_timeout(
            "report",
            AGENT_TIMEOUTS["report"],
            crew.kickoff,
            inputs={
                "user_message": user_message,
                "workflow_status": workflow_status,
                "step_results": json.dumps(
                    _compact(step_results),
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        )
        markdown = _extract_markdown(output)
        if not markdown:
            LOGGER.warning("Report agent returned empty output; skipping write.")
            return None

        path = _write_report(workflow_id, markdown)
        LOGGER.info("Workflow report written to %s", path)
        return {"report_path": str(path)}
    except Exception:
        LOGGER.exception("Workflow report generation failed (non-fatal)")
        return None


def _should_generate(step_results: list[dict[str, Any]]) -> bool:
    return any(
        step.get("intent") not in _SKIP_INTENTS
        for step in step_results
    )


def _compact(value: Any) -> Any:
    """Strip bulky execution traces and clip long strings so the report
    prompt stays within a reasonable token budget."""
    if isinstance(value, dict):
        return {
            key: _compact(item)
            for key, item in value.items()
            if key != "agent_execution"
        }
    if isinstance(value, list):
        return [_compact(item) for item in value]
    if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
        return value[:_MAX_FIELD_CHARS] + "…(truncated)"
    return value


def _extract_markdown(output: Any) -> str:
    text = str(getattr(output, "raw", None) or output or "").strip()
    # Unwrap a fenced block if the model wrapped the whole report in one.
    match = re.fullmatch(r"```(?:markdown)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return text


def _write_report(workflow_id: str, markdown: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", workflow_id)[:8] or "workflow"
    path = REPORTS_DIR / f"report_{timestamp}_{safe_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
