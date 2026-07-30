import logging
from typing import Any

from defectdojo_crewai.config import llm_config
from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.memory.token_counter import (
    count_message_tokens,
    count_text_tokens,
    truncate_text_tokens,
)


LOGGER = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You maintain a compact memory summary for a vulnerability-management "
    "assistant. Preserve user goals, confirmed constraints, decisions, completed "
    "work, unresolved questions, and exact identifiers or file paths. Do not "
    "invent facts or instructions. Return only the updated summary without "
    "Markdown fences."
)


def summarize_incrementally(
    previous_summary: str,
    messages: list[dict[str, Any]],
) -> str:
    if not messages:
        return truncate_text_tokens(
            previous_summary,
            settings.context_summary_token_budget,
        )

    summary = previous_summary
    for chunk in _message_chunks(
        messages,
        settings.context_summary_input_token_budget,
    ):
        try:
            summary = _summarize_chunk(summary, chunk)
        except Exception:
            LOGGER.exception("Conversation summarization failed; using safe fallback")
            summary = _fallback_summary(summary, chunk)
    return truncate_text_tokens(
        summary,
        settings.context_summary_token_budget,
    )


def _summarize_chunk(
    previous_summary: str,
    messages: list[dict[str, Any]],
) -> str:
    transcript = "\n".join(
        f"{message.get('role', 'unknown')}: {message.get('content', '')}"
        for message in messages
    )
    prompt = (
        "Previous summary:\n"
        f"{previous_summary or '(none)'}\n\n"
        "New older messages to merge:\n"
        f"{transcript}\n\n"
        f"Keep the updated summary within "
        f"{settings.context_summary_token_budget} tokens."
    )
    llm = llm_config.getLLM(
        max_tokens=settings.context_summary_token_budget,
    )
    response = llm.call(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
    )
    return truncate_text_tokens(
        str(response).strip(),
        settings.context_summary_token_budget,
    )


def _message_chunks(
    messages: list[dict[str, Any]],
    max_tokens: int,
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    used = 0
    for message in messages:
        message_tokens = count_message_tokens(message)
        if message_tokens > max_tokens:
            role_tokens = count_message_tokens(
                {"role": message.get("role"), "content": ""},
            )
            message = {
                **message,
                "content": truncate_text_tokens(
                    str(message.get("content") or ""),
                    max(max_tokens - role_tokens, 1),
                ),
            }
            message_tokens = count_message_tokens(message)
        if current and used + message_tokens > max_tokens:
            chunks.append(current)
            current = []
            used = 0
        current.append(message)
        used += message_tokens
    if current:
        chunks.append(current)
    return chunks


def _fallback_summary(
    previous_summary: str,
    messages: list[dict[str, Any]],
) -> str:
    lines = [previous_summary.strip()] if previous_summary.strip() else []
    lines.extend(
        f"{message.get('role', 'unknown')}: "
        f"{' '.join(str(message.get('content') or '').split())}"
        for message in messages
    )
    combined = "\n".join(line for line in lines if line)
    if count_text_tokens(combined) <= settings.context_summary_token_budget:
        return combined
    return truncate_text_tokens(
        combined,
        settings.context_summary_token_budget,
    )
