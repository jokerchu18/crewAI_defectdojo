from crewai import LLM
import os

def getLLM() -> LLM:
    return LLM(
    model=os.getenv("model"),
    base_url=os.getenv("base_url"),
    api_key=os.getenv("api_key")
)