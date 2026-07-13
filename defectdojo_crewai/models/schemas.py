from pydantic import BaseModel, Field

# risk_acceptance_review_agent's ouput

class RiskAcceptanceCandidate(BaseModel):
    finding_id: int = Field(..., description="DefectDojo finding id")
    severity: str = Field(..., description="Finding severity")
    title: str = Field(..., description="Finding title")
    decision: str = Field(..., description="Accept or Reject")
    reason: str = Field(..., description="Reason for the decision")
    expiration_date: str = Field(..., description="Suggested expiration date, format: YYYY-MM-DD")
    reactivate_expired: bool = Field(..., description="Whether to reactivate when expired")
    restart_sla_expired: bool = Field(..., description="Whether to restart SLA when expired")


class RiskAcceptanceReviewResult(BaseModel):
    candidates: list[RiskAcceptanceCandidate] = Field(
        default_factory=list,
        description="Risk acceptance review candidates"
    )


