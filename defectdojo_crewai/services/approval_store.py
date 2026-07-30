import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from defectdojo_crewai.models.schemas import PendingApproval


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _database_path() -> Path:
    configured_path = os.getenv("APPROVAL_DATABASE_PATH")
    if not configured_path:
        return PROJECT_ROOT / "data" / "approvals.db"

    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


DATABASE_PATH = _database_path()


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_approval_store() -> None:
    with _connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                workflow_id TEXT,
                step_id TEXT,
                action_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                payload TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                status TEXT NOT NULL,
                reviewer TEXT,
                reviewer_comment TEXT,
                result TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(approvals)").fetchall()
        }
        if "step_id" not in columns:
            connection.execute("ALTER TABLE approvals ADD COLUMN step_id TEXT")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_approvals_workflow_step
            ON approvals (workflow_id, step_id, status)
            """
        )


def create_approval(action: PendingApproval) -> dict[str, Any]:
    approval_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO approvals (
                approval_id, workflow_id, step_id, action_type, title, description,
                payload, risk_level, requested_by, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                action.workflow_id,
                action.step_id,
                action.action_type,
                action.title,
                action.description,
                json.dumps(action.payload, ensure_ascii=False),
                action.risk_level,
                action.requested_by,
                "pending",
                now,
                now,
            ),
        )

    return get_approval(approval_id)


def get_approval(approval_id: str) -> dict[str, Any] | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()

    return _deserialize(row)


def list_pending_approvals() -> list[dict[str, Any]]:
    with _connection() as connection:
        rows = connection.execute(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()

    return [_deserialize(row) for row in rows]


def list_step_approvals(
    workflow_id: str,
    step_id: str,
) -> list[dict[str, Any]]:
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM approvals
            WHERE workflow_id = ? AND step_id = ?
            ORDER BY created_at
            """,
            (workflow_id, step_id),
        ).fetchall()
    return [_deserialize(row) for row in rows]


def cancel_pending_step_approvals(
    workflow_id: str,
    step_id: str,
    *,
    except_approval_id: str,
) -> None:
    now = datetime.now().isoformat()
    with _connection() as connection:
        connection.execute(
            """
            UPDATE approvals
            SET status = 'cancelled', updated_at = ?
            WHERE workflow_id = ? AND step_id = ?
              AND approval_id != ? AND status = 'pending'
            """,
            (now, workflow_id, step_id, except_approval_id),
        )


def update_approval(
    approval_id: str,
    *,
    status: str,
    reviewer: str | None = None,
    reviewer_comment: str | None = None,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    fields = ["status = ?", "updated_at = ?"]
    values: list[Any] = [status, datetime.now().isoformat()]

    optional_values = {
        "reviewer": reviewer,
        "reviewer_comment": reviewer_comment,
        "payload": json.dumps(payload, ensure_ascii=False) if payload is not None else None,
        "result": json.dumps(result, ensure_ascii=False) if result is not None else None,
        "error_message": error_message,
    }

    for field, value in optional_values.items():
        if value is not None:
            fields.append(f"{field} = ?")
            values.append(value)

    values.append(approval_id)

    with _connection() as connection:
        connection.execute(
            f"UPDATE approvals SET {', '.join(fields)} WHERE approval_id = ?",
            values,
        )


def claim_pending_approval(
    approval_id: str,
    *,
    reviewer: str,
    reviewer_comment: str,
    payload: dict[str, Any],
) -> bool:
    now = datetime.now().isoformat()
    with _connection() as connection:
        cursor = connection.execute(
            """
            UPDATE approvals
            SET status = 'executing', reviewer = ?, reviewer_comment = ?,
                payload = ?, updated_at = ?
            WHERE approval_id = ? AND status = 'pending'
            """,
            (
                reviewer,
                reviewer_comment,
                json.dumps(payload, ensure_ascii=False),
                now,
                approval_id,
            ),
        )
        return cursor.rowcount == 1


def _deserialize(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None

    approval = dict(row)
    for field in ("payload", "result"):
        value = approval.get(field)
        if value:
            approval[field] = json.loads(value)
    return approval
