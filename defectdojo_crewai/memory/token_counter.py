import os
from functools import lru_cache
from typing import Any

import tiktoken


_FALLBACK_ENCODING = "cl100k_base"


@lru_cache(maxsize=8)
def _encoding(model: str | None = None):
    configured = os.getenv("TOKENIZER_MODEL", "").strip()
    model_name = configured or (model or os.getenv("model", "")).strip()
    if model_name:
        try:
            return tiktoken.encoding_for_model(model_name)
        except KeyError:
            pass
    return tiktoken.get_encoding(_FALLBACK_ENCODING)


def count_text_tokens(text: str, *, model: str | None = None) -> int:
    if not text:
        return 0
    return len(_encoding(model).encode(text))


def count_message_tokens(message: dict[str, Any], *, model: str | None = None) -> int:
    # Include a small role/framing allowance matching chat-message serialization.
    return 4 + count_text_tokens(str(message.get("role") or ""), model=model) + (
        count_text_tokens(str(message.get("content") or ""), model=model)
    )


def truncate_text_tokens(
    text: str,
    max_tokens: int,
    *,
    model: str | None = None,
) -> str:
    if max_tokens <= 0 or not text:
        return ""
    encoding = _encoding(model)
    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return encoding.decode(tokens[:max_tokens])
