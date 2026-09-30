"""Hypothesis and Critic schemas — output of the Investigator and Critic stages."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from oncall_rca.schemas.code_context import CodeFile
from oncall_rca.schemas.evidence import Citation


class Confidence(str, Enum):
    """Confidence level assigned by the Critic."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Hypothesis(BaseModel):
    """A root-cause hypothesis produced by the Investigator.

    Produced by: Investigator stage.
    Consumed by: Critic, RCA Writer.
    """

    claim: str = Field(description="One-sentence root cause claim")
    explanation: str = Field(description="Detailed explanation of the root cause")
    supporting_citations: list[Citation] = Field(default_factory=list)
    contradicting_evidence: list[Citation] = Field(
        default_factory=list,
        description="Evidence that weakens or contradicts this hypothesis",
    )
    affected_code: list[CodeFile] = Field(
        default_factory=list,
        description="Code files/functions implicated in the root cause",
    )
    proposed_fix: str = Field(
        default="",
        description="Approach to fix (spec, not patch)",
    )


class CitationCheck(BaseModel):
    """Result of mechanically verifying one citation."""

    citation: Citation
    exists: bool = Field(description="Whether the cited file/field was found")
    says_what_claimed: bool = Field(
        description="Whether the cited content matches the claim",
    )
    note: str = Field(default="", description="Discrepancy detail if check failed")


class Alternative(BaseModel):
    """An alternative explanation the Critic considered."""

    claim: str
    why_ranked_lower: str


class CriticReport(BaseModel):
    """Independent verification of a hypothesis.

    Produced by: Critic stage (fresh context, never sees Investigator reasoning).
    Consumed by: RCA Writer.
    """

    citation_checks: list[CitationCheck] = Field(default_factory=list)
    all_citations_valid: bool = Field(
        default=False,
        description="True only if every citation exists and says what is claimed",
    )
    alternatives: list[Alternative] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    rubric_scores: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Rubric dimensions: citation_coverage, alternatives_ruled_out, "
            "timeline_consistent, all_symptoms_explained, reproduction_path_exists"
        ),
    )
    notes: str = Field(default="", description="Free-form Critic commentary")
