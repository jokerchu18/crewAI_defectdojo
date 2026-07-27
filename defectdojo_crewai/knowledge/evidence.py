"""Deterministic trust checks for retrieved knowledge evidence."""

from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field

from defectdojo_crewai.knowledge.storage import SOURCE_AUDIT
from defectdojo_crewai.models.schemas import IntentName


class DecisionHistoryEvidence(BaseModel):
    trusted: bool
    intent: IntentName | None = None
    matches: list[dict[str, Any]] = Field(default_factory=list)
    best_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: Literal[
        "trusted_consensus",
        "no_eligible_history",
        "insufficient_consensus",
        "conflicting_consensus",
    ]


def assess_decision_history(
    matches: list[dict[str, Any]],
    *,
    min_similarity: float,
    min_consensus: int,
) -> DecisionHistoryEvidence:
    """Accept only completed, high-similarity history with a clear consensus."""
    eligible_by_intent: dict[IntentName, list[dict[str, Any]]] = defaultdict(list)
    for match in matches:
        intent = _eligible_intent(match, min_similarity)
        if intent is not None:
            eligible_by_intent[intent].append(match)

    if not eligible_by_intent:
        return DecisionHistoryEvidence(reason="no_eligible_history", trusted=False)

    ranked = sorted(
        eligible_by_intent.items(),
        key=lambda item: (
            len(item[1]),
            sum(_score(match) for match in item[1]),
        ),
        reverse=True,
    )
    winning_intent, winning_matches = ranked[0]
    if len(winning_matches) < min_consensus:
        return DecisionHistoryEvidence(
            reason="insufficient_consensus",
            trusted=False,
        )
    if len(ranked) > 1 and len(ranked[1][1]) == len(winning_matches):
        return DecisionHistoryEvidence(
            reason="conflicting_consensus",
            trusted=False,
        )
    return DecisionHistoryEvidence(
        reason="trusted_consensus",
        trusted=True,
        intent=winning_intent,
        matches=winning_matches,
        best_similarity=max(_score(match) for match in winning_matches),
    )


def _eligible_intent(
    match: dict[str, Any],
    min_similarity: float,
) -> IntentName | None:
    metadata = match.get("metadata")
    if not isinstance(metadata, dict):
        return None
    intent = metadata.get("intent")
    if intent not in {
        "risk_acceptance",
        "deduplication",
        "triage",
        "remediation",
        "verification",
        "import_scan",
        "query_findings",
    }:
        return None
    if metadata.get("source_type") != SOURCE_AUDIT:
        return None
    if metadata.get("outcome") != "completed":
        return None
    if metadata.get("verification_status") != "observed":
        return None
    if _score(match) < min_similarity:
        return None
    return intent


def _score(match: dict[str, Any]) -> float:
    score = match.get("score")
    return float(score) if isinstance(score, int | float) else 0.0
