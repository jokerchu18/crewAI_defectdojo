from typing import Any, Literal

from pydantic import BaseModel, Field

from defectdojo_crewai.models.schemas import ConversationContext


class ConversationMessage(BaseModel):
    message_id: int | None = None
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: float | None = None


class ConversationHistory(BaseModel):
    summary: str = ""
    recent_messages: list[ConversationMessage] = Field(default_factory=list)
    total_messages: int = 0


class AgentExecutionRecord(BaseModel):
    agent: str
    task: str
    raw_output: str = ""
    structured_output: Any = None
    task_outputs: list[dict[str, Any]] = Field(default_factory=list)
    token_usage: dict[str, Any] = Field(default_factory=dict)


class WorkflowStepContext(BaseModel):
    workflow_id: str | None = None
    step_id: str
    intent: str
    status: str
    summary: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    agent_execution: AgentExecutionRecord | None = None
    source: Literal["history", "current"] = "current"


class WorkflowContext(BaseModel):
    steps: list[WorkflowStepContext] = Field(default_factory=list)


class AgentContext(BaseModel):
    current_request: str
    target_agent: str
    business_context: ConversationContext
    conversation_history: ConversationHistory
    workflow_context: WorkflowContext


class MemorySnapshot(BaseModel):
    conversation_history: ConversationHistory
    workflow_context: WorkflowContext
