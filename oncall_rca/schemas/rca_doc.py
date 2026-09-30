"""RCA document schema — output of the RCA Writer stage."""

from __future__ import annotations

from pydantic import BaseModel, Field

from oncall_rca.schemas.code_context import CodeFile
from oncall_rca.schemas.evidence import Citation, DivergencePoint
from oncall_rca.schemas.hypothesis import Alternative, Confidence


class TimelineEntry(BaseModel):
    """A key step in the incident timeline."""

    timestamp: str
    service: str
    pod: str
    description: str
    file_ref: str = Field(default="", description="Log file name for reference")


class RCADoc(BaseModel):
    """Structured RCA document matching the template in design §13.

    Produced by: RCA Writer stage.
    Consumed by: Human review, Claude Code handoff.
    """

    trip_id: str
    confidence: Confidence

    # Summary
    summary: str = Field(description="One-paragraph root cause")

    # Incident
    reported_symptom: str
    itinerary_ids: list[str] = Field(default_factory=list)
    journeys: list[str] = Field(
        default_factory=list,
        description="e.g. ['COK→CAI (3L)', 'CAI→COK (G9)']",
    )
    passengers: int = 0

    # Timeline
    timeline: list[TimelineEntry] = Field(default_factory=list)

    # Divergence
    divergence_point: DivergencePoint | None = None

    # Root Cause
    root_cause: str = Field(default="", description="Explanation with citations")
    root_cause_citations: list[Citation] = Field(default_factory=list)

    # Alternatives
    alternatives: list[Alternative] = Field(default_factory=list)

    # Affected Code
    affected_code: list[CodeFile] = Field(default_factory=list)
    commit_shas: dict[str, str] = Field(default_factory=dict)
    changed_after_incident_warning: list[str] = Field(default_factory=list)

    # Fix
    proposed_fix: str = ""

    # Reproduction & Tests
    reproduction_payloads: list[str] = Field(
        default_factory=list,
        description="Request payload references to reproduce the issue",
    )
    suggested_tests: list[str] = Field(default_factory=list)

    # Acceptance Criteria
    acceptance_criteria: list[str] = Field(default_factory=list)

    # Risks
    risks: list[str] = Field(default_factory=list)

    # Evidence file references
    evidence_files: list[str] = Field(
        default_factory=list,
        description="Paths relative to evidence/ folder",
    )
