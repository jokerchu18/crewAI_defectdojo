from crewai import LLM;

def getLLM() -> LLM:
    return LLM(
    model="gpt-4o",
    base_url="https://apis.itedus.cn/v1",
    api_key="sk-8F6xHw1Rd1mX6uy66771344d7fC74dDa903eD6470f008dD6"
)