"""PostgreSQL-backed persistence for per-session chat message history.

Session context stays in Redis (it is small and TTL-driven); the chat
transcript goes to Postgres so it survives Redis restarts and can be
queried/audited. Retention mirrors the Redis policy: per-session message
cap plus a time-based cutoff aligned with the session TTL.
"""

import atexit
import json
import logging
from threading import Lock
from typing import Any

from psycopg import Error as PostgresDriverError
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from defectdojo_crewai.config.settings import settings


_LOCK = Lock()
_POOL: ConnectionPool | None = None

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages (session_id, id);
"""


class MessageStoreError(RuntimeError):
    """Raised when the Postgres-backed message store cannot complete an operation."""


def init_message_store() -> None:
    """Create the connection pool and schema; fail fast when Postgres is down."""
    try:
        with _get_pool().connection() as conn:
            conn.execute(_SCHEMA_SQL)
    except PostgresDriverError as exc:
        raise MessageStoreError(_CONNECT_HINT) from exc


def append_message(
    session_id: str,
    role: str,
    content: str,
    *,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_session_id(session_id)
    payload = (
        Jsonb(json.loads(json.dumps(result, ensure_ascii=False, default=str)))
        if result is not None
        else None
    )
    try:
        with _get_pool().connection() as conn:
            row = conn.execute(
                """
                INSERT INTO chat_messages (session_id, role, content, result)
                VALUES (%s, %s, %s, %s)
                RETURNING id, extract(epoch FROM created_at) AS created_at
                """,
                (normalized, role, content, payload),
            ).fetchone()
            _prune(conn, normalized)
    except PostgresDriverError as exc:
        raise MessageStoreError(_CONNECT_HINT) from exc

    return {
        "role": role,
        "content": content,
        "created_at": float(row[1]),
        **({"result": result} if result is not None else {}),
    }


def get_messages(session_id: str) -> list[dict[str, Any]]:
    normalized = _normalize_session_id(session_id)
    try:
        with _get_pool().connection() as conn, conn.cursor(
            row_factory=dict_row
        ) as cursor:
            rows = cursor.execute(
                """
                SELECT role, content, result,
                       extract(epoch FROM created_at) AS created_at
                FROM chat_messages
                WHERE session_id = %s
                  AND created_at > now() - make_interval(secs => %s)
                ORDER BY id
                """,
                (normalized, settings.session_ttl_seconds),
            ).fetchall()
    except PostgresDriverError as exc:
        raise MessageStoreError(_CONNECT_HINT) from exc

    messages: list[dict[str, Any]] = []
    for row in rows:
        message: dict[str, Any] = {
            "role": row["role"],
            "content": row["content"],
            "created_at": float(row["created_at"]),
        }
        if row["result"] is not None:
            message["result"] = row["result"]
        messages.append(message)
    return messages


def clear_messages(session_id: str) -> None:
    normalized = _normalize_session_id(session_id)
    try:
        with _get_pool().connection() as conn:
            conn.execute(
                "DELETE FROM chat_messages WHERE session_id = %s",
                (normalized,),
            )
    except PostgresDriverError as exc:
        raise MessageStoreError(_CONNECT_HINT) from exc


def close_message_store() -> None:
    global _POOL
    with _LOCK:
        pool = _POOL
        _POOL = None
    if pool is not None:
        pool.close()


_CONNECT_HINT = (
    "Postgres message store operation failed. Check CHAT_DATABASE_URL and "
    "confirm that the postgres container (defectdojo-chat-postgres) is running."
)


def _prune(conn, session_id: str) -> None:
    """Enforce per-session cap and drop expired rows for this session."""
    conn.execute(
        """
        DELETE FROM chat_messages
        WHERE session_id = %(sid)s
          AND (
            id NOT IN (
                SELECT id FROM chat_messages
                WHERE session_id = %(sid)s
                ORDER BY id DESC
                LIMIT %(cap)s
            )
            OR created_at <= now() - make_interval(secs => %(ttl)s)
          )
        """,
        {
            "sid": session_id,
            "cap": settings.session_history_max_messages,
            "ttl": settings.session_ttl_seconds,
        },
    )


def _get_pool() -> ConnectionPool:
    global _POOL
    with _LOCK:
        if _POOL is None:
            _POOL = ConnectionPool(
                settings.chat_database_url,
                min_size=1,
                max_size=settings.chat_database_pool_size,
                timeout=settings.chat_database_timeout_seconds,
                kwargs={"autocommit": True},
                open=True,
            )
        return _POOL


def _normalize_session_id(session_id: str) -> str:
    normalized = session_id.strip()
    if not normalized:
        raise ValueError("session_id cannot be empty.")
    if len(normalized) > 256:
        raise ValueError("session_id cannot exceed 256 characters.")
    return normalized


logging.getLogger("psycopg.pool").setLevel(logging.WARNING)
atexit.register(close_message_store)
