from threading import RLock
from typing import Any

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.models.schemas import ConversationContext


_LOCK = RLock()
_CLIENT: Redis | None = None


class SessionStoreError(RuntimeError):
    """Raised when the Redis-backed session store cannot complete an operation."""


def init_session_store() -> None:
    """Initialize the Redis client and fail fast when Redis is unavailable."""
    _execute_redis("ping")


def get_session_context(session_id: str) -> ConversationContext:
    key = _session_key(session_id)
    payload = _execute_redis("get", key)
    if payload is None:
        return ConversationContext()

    try:
        context = ConversationContext.model_validate_json(payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise SessionStoreError(
            f"Session {session_id!r} contains invalid context data."
        ) from exc

# 每次获取缓存时刷新过期时间
    if settings.session_refresh_ttl_on_read:
        _execute_redis("expire", key, settings.session_ttl_seconds)
    return context


def save_session_context(
    session_id: str,
    context: ConversationContext,
) -> ConversationContext:
    saved = context.model_copy(deep=True)
    _execute_redis(
        "set",
        _session_key(session_id),
        saved.model_dump_json(),
        ex=settings.session_ttl_seconds,
    )
    return saved.model_copy(deep=True)


def clear_session_context(session_id: str) -> None:
    _execute_redis("delete", _session_key(session_id))


def close_session_store() -> None:
    """Close the process-local Redis connection pool."""
    global _CLIENT
    with _LOCK:
        client = _CLIENT
        _CLIENT = None
    if client is not None:
        client.close()


def _redis_client() -> Redis:
    global _CLIENT
    with _LOCK:
        if _CLIENT is None:
            _CLIENT = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=settings.redis_socket_timeout_seconds,
                socket_timeout=settings.redis_socket_timeout_seconds,
                health_check_interval=30,
            )
        return _CLIENT


def _execute_redis(command: str, *args: Any, **kwargs: Any) -> Any:
    try:
        operation = getattr(_redis_client(), command)
        return operation(*args, **kwargs)
    except RedisError as exc:
        raise SessionStoreError(
            "Redis session operation failed. Check REDIS_URL and confirm "
            "that the Redis server is running."
        ) from exc


def _session_key(session_id: str) -> str:
    normalized = session_id.strip()
    if not normalized:
        raise ValueError("session_id cannot be empty.")
    if len(normalized) > 256:
        raise ValueError("session_id cannot exceed 256 characters.")
    return f"{settings.session_redis_prefix}:{normalized}"
