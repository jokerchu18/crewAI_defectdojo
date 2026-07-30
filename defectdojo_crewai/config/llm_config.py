import os

from crewai import LLM


def getLLM(*, max_tokens: int | None = None) -> LLM:
    return LLM(
        model=os.getenv("model"),
        base_url=os.getenv("base_url"),
        api_key=os.getenv("api_key"),
        max_tokens=max_tokens,
    )
