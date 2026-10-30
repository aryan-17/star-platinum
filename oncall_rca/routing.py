"""Confidence-based auto-routing — maps critic confidence to an action.

HIGH confidence → configurable action (default: human_review, switch to
auto_fix once eval data calibrates the threshold).
MEDIUM → human_review.
LOW → escalate.
"""

from __future__ import annotations

from enum import Enum

from pydantic_settings import BaseSettings

from oncall_rca.schemas.hypothesis import Confidence, CriticReport


class RoutingDecision(str, Enum):
    """Post-RCA action."""

    AUTO_FIX = "auto_fix"
    HUMAN_REVIEW = "human_review"
    ESCALATE = "escalate"


class RoutingSettings(BaseSettings):
    """Configurable routing thresholds."""

    high_confidence_action: str = "human_review"
    low_confidence_action: str = "escalate"


_CONFIDENCE_RANK: dict[Confidence, int] = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}


def route_by_confidence(
    critic_reports: dict[int, CriticReport],
    settings: RoutingSettings,
) -> RoutingDecision:
    """Determine the post-RCA routing action from critic confidence.

    Takes the worst (lowest) confidence across all journey reports.
    Empty reports default to ESCALATE.
    """
    if not critic_reports:
        return RoutingDecision.ESCALATE

    worst = min(
        (r.confidence for r in critic_reports.values()),
        key=lambda c: _CONFIDENCE_RANK[c],
    )

    if worst == Confidence.HIGH:
        return RoutingDecision(settings.high_confidence_action)
    if worst == Confidence.LOW:
        return RoutingDecision(settings.low_confidence_action)
    return RoutingDecision.HUMAN_REVIEW
