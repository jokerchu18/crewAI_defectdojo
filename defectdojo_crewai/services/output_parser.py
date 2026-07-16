import ast
import json
from typing import Any, TypeVar

from pydantic import BaseModel


ModelT = TypeVar("ModelT", bound=BaseModel)


def parse_model_output(output: Any, model: type[ModelT]) -> ModelT:
    """Parse normal LLM text locally without using provider response_format."""
    if isinstance(output, model):
        return output
    if isinstance(output, dict):
        return model.model_validate(output)

    # CrewOutput and TaskOutput are Pydantic models too, but their target data
    # lives in pydantic/json_dict/raw rather than at the outer model level.
    pydantic_output = getattr(output, "pydantic", None)
    if pydantic_output is not None:
        return parse_model_output(pydantic_output, model)

    json_output = getattr(output, "json_dict", None)
    if json_output is not None:
        return model.model_validate(json_output)

    raw = getattr(output, "raw", None)
    if raw is not None:
        return parse_model_output(raw, model)

    if isinstance(output, BaseModel):
        return model.model_validate(output.model_dump())

    raw = output
    if not isinstance(raw, str):
        return model.model_validate(raw)

    text = raw.strip()
    candidates = [text, *_json_fragments(text)]
    for candidate in candidates:
        try:
            return model.model_validate(json.loads(candidate))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        try:
            value = ast.literal_eval(candidate)
        except (SyntaxError, ValueError):
            continue

        try:
            return model.model_validate(value)
        except (ValueError, TypeError):
            continue

    preview = text[:500].replace("\n", " ")
    raise ValueError(
        f"LLM output could not be parsed as {model.__name__}: {preview}"
    )


def _json_fragments(text: str) -> list[str]:
    decoder = json.JSONDecoder()
    fragments: list[str] = []

    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            _, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        fragments.append(text[index:index + end])

    return fragments
