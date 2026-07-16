from threading import RLock

from defectdojo_crewai.models.schemas import ConversationContext


_LOCK = RLock()
_SESSIONS: dict[str, ConversationContext] = {}


def get_session_context(session_id: str) -> ConversationContext:
    with _LOCK:
        context = _SESSIONS.get(session_id)
        if context is None:
            return ConversationContext()
        return context.model_copy(deep=True)


def save_session_context(
    session_id: str,
    context: ConversationContext,
) -> ConversationContext:
    saved = context.model_copy(deep=True)
    with _LOCK:
        _SESSIONS[session_id] = saved
    return saved.model_copy(deep=True)


def clear_session_context(session_id: str) -> None:
    with _LOCK:
        _SESSIONS.pop(session_id, None)
