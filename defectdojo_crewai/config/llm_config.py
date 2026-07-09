from crewai import LLM;
import os

def getLLM() -> LLM:
    return LLM(
    model=os.getenv("model"),
    base_url=os.getenv("https://apis.itedus.cn/v1"),
    api_key=os.getenv("sk-8F6xHw1Rd1mX6uy66771344d7fC74dDa903eD6470f008dD6")
)