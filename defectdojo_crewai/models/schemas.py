from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# risk_acceptance_review_agent's ouput

class RiskAcceptanceCandidate(BaseModel):
    finding_id: int = Field(..., description="DefectDojo finding id")
    severity: str = Field(..., description="Finding severity")
    title: str = Field(..., description="Finding title")
    decision: Literal["Accept", "Reject"] = Field(..., description="Accept or Reject")
    reason: str = Field(..., description="Reason for the decision")
    expiration_date: str | None = Field(
        default=None,
        description="Suggested expiration date, format: YYYY-MM-DD",
    )
    reactivate_expired: bool = Field(..., description="Whether to reactivate when expired")
    restart_sla_expired: bool = Field(..., description="Whether to restart SLA when expired")


class RiskAcceptanceReviewResult(BaseModel):
    candidates: list[RiskAcceptanceCandidate] = Field(
        default_factory=list,
        description="Risk acceptance review candidates"
    )


class RiskAcceptanceExecutionItem(BaseModel):
    finding_id: int
    risk_acceptance_created: bool
    finding_updated: bool
    message: str


class RiskAcceptanceExecutionResult(BaseModel):
    results: list[RiskAcceptanceExecutionItem] = Field(default_factory=list)


IntentName = Literal[
    "risk_acceptance",
    "deduplication",
    "triage",
    "remediation",
    "verification",
    "import_scan",
    "query_findings",
    "unknown",
]


class UserIntent(BaseModel):
    # forbid extras so a lone WorkflowStep fragment cannot masquerade as an
    # intent (or plan) when the router emits malformed JSON
    model_config = ConfigDict(extra="forbid")

    intent: IntentName
    product_id: int | None = None
    test_id: int | None = None
    finding_ids: list[int] = Field(default_factory=list)
    severity: str | None = None
    engagement_id: int | None = None
    scan_type: str | None = None
    file_path: str | None = None
    message: str = ""


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(..., description="Unique step id in this workflow")
    intent: IntentName
    product_id: int | None = None
    test_id: int | None = None
    finding_ids: list[int] = Field(default_factory=list)
    severity: str | None = None
    engagement_id: int | None = None
    scan_type: str | None = None
    file_path: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    instruction: str = ""

    def to_user_intent(self) -> UserIntent:
        return UserIntent(
            intent=self.intent,
            product_id=self.product_id,
            test_id=self.test_id,
            finding_ids=self.finding_ids,
            severity=self.severity,
            engagement_id=self.engagement_id,
            scan_type=self.scan_type,
            file_path=self.file_path,
            message=self.instruction,
        )


class WorkflowPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[WorkflowStep] = Field(default_factory=list)
    message: str = ""


class ConversationContext(BaseModel):
    test_id: int | None = None
    product_id: int | None = None
    engagement_id: int | None = None
    finding_ids: list[int] = Field(default_factory=list)
    scan_type: str | None = None
    file_path: str | None = None


class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    context: ConversationContext = Field(default_factory=ConversationContext)


class ChatResponse(BaseModel):
    session_id: str
    intent: UserIntent
    plan: WorkflowPlan | None = None
    context: ConversationContext
    result: dict[str, Any]


class PendingApproval(BaseModel):
    action_type: str
    title: str
    description: str
    payload: dict[str, Any]
    risk_level: Literal["low", "medium", "high", "critical"] = "high"
    workflow_id: str | None = None
    requested_by: str = "router_agent"


class ApprovalDecision(BaseModel):
    approval_id: str
    decision: Literal["approve", "reject"]
    reviewer: str = "human"
    comment: str = ""
    approved_finding_ids: list[int] = Field(default_factory=list)
    edited_payload: dict[str, Any] | None = None


