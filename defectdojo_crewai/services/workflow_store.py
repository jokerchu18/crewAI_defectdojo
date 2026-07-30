import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field

from defectdojo_crewai.memory.models import ConversationHistory, WorkflowContext
from defectdojo_crewai.models.schemas import ConversationContext, WorkflowPlan


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _database_path() -> Path:
    configured_path = os.getenv("WORKFLOW_DATABASE_PATH")
    if not configured_path:
        return PROJECT_ROOT / "data" / "workflows.db"

    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


DATABASE_PATH = _database_path()


class WorkflowRun(BaseModel):
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    status: str = "running"
    plan: WorkflowPlan
    current_step_index: int = 0
    completed_step_ids: list[str] = Field(default_factory=list)
    context: ConversationContext = Field(default_factory=ConversationContext)
    explicit_context: ConversationContext = Field(default_factory=ConversationContext)
    conversation_history: ConversationHistory = Field(
        default_factory=ConversationHistory
    )
    workflow_context: WorkflowContext = Field(default_factory=WorkflowContext)
    step_results: list[dict[str, Any]] = Field(default_factory=list)
    user_message: str
    representative_intent: dict[str, Any] | None = None
    version: int = 0
    created_at: str | None = None
    updated_at: str | None = None


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_workflow_store() -> None:
    with _connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                workflow_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_runs_session
            ON workflow_runs (session_id, created_at)
            """
        )


def create_workflow_run(run: WorkflowRun) -> WorkflowRun:
    now = datetime.now().isoformat()
    saved = run.model_copy(
        deep=True,
        update={"version": 0, "created_at": now, "updated_at": now},
    )
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO workflow_runs (
                workflow_id, session_id, status, payload, version,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                saved.workflow_id,
                saved.session_id,
                saved.status,
                _serialize(saved),
                saved.version,
                now,
                now,
            ),
        )
    return saved


def get_workflow_run(workflow_id: str) -> WorkflowRun | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM workflow_runs WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
    return _deserialize(row)


def save_workflow_run(run: WorkflowRun) -> WorkflowRun:
    now = datetime.now().isoformat()
    next_version = run.version + 1
    saved = run.model_copy(
        deep=True,
        update={"version": next_version, "updated_at": now},
    )
    with _connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_runs
            SET session_id = ?, status = ?, payload = ?, version = ?,
                updated_at = ?
            WHERE workflow_id = ? AND version = ?
            """,
            (
                saved.session_id,
                saved.status,
                _serialize(saved),
                next_version,
                now,
                saved.workflow_id,
                run.version,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Workflow {run.workflow_id} was updated concurrently."
            )
    return saved


def claim_workflow_resume(workflow_id: str) -> WorkflowRun | None:
    now = datetime.now().isoformat()
    with _connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_runs
            SET status = 'resuming', version = version + 1, updated_at = ?
            WHERE workflow_id = ? AND status = 'waiting_approval'
            """,
            (now, workflow_id),
        )
        if cursor.rowcount != 1:
            return None
        row = connection.execute(
            "SELECT * FROM workflow_runs WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
    return _deserialize(row)


def set_workflow_status(
    workflow_id: str,
    status: str,
    *,
    only_if: tuple[str, ...] = ("waiting_approval", "resuming"),
) -> bool:
    placeholders = ", ".join("?" for _ in only_if)
    now = datetime.now().isoformat()
    with _connection() as connection:
        cursor = connection.execute(
            f"""
            UPDATE workflow_runs
            SET status = ?, version = version + 1, updated_at = ?
            WHERE workflow_id = ? AND status IN ({placeholders})
            """,
            (status, now, workflow_id, *only_if),
        )
        return cursor.rowcount == 1


def _serialize(run: WorkflowRun) -> str:
    return run.model_dump_json()


def _deserialize(row: sqlite3.Row | None) -> WorkflowRun | None:
    if row is None:
        return None
    payload = json.loads(row["payload"])
    payload.update(
        {
            "status": row["status"],
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )
    return WorkflowRun.model_validate(payload)
