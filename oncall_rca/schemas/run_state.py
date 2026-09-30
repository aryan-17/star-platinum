"""RunState schema — the single object passed through the LangGraph workflow."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from oncall_rca.schemas.code_context import CodeContext
from oncall_rca.schemas.evidence import EvidencePack
from oncall_rca.schemas.hypothesis import CriticReport, Hypothesis
from oncall_rca.schemas.incident import Incident
from oncall_rca.schemas.rca_doc import RCADoc
from oncall_rca.schemas.trip_index import TripIndex


class StageStatus(str, Enum):
    """Status of a workflow stage."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StageRecord(BaseModel):
    """Record of one stage's execution."""

    stage: str
    status: StageStatus = StageStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    tokens_used: int = 0
    cost_usd: float = 0.0


class RunState(BaseModel):
    """The single state object threaded through the LangGraph workflow.

    Immutable between stages: each stage returns a new RunState.
    Checkpointed after each stage for resumability.

    Produced by: each stage (progressively filled).
    Consumed by: the next stage and the workflow controller.
    """

    run_id: str = Field(description="Unique run identifier")
    started_at: datetime = Field(default_factory=datetime.utcnow)

    # Stage outputs (filled progressively)
    incident: Incident | None = None
    trip_index: TripIndex | None = None
    evidence_pack: EvidencePack | None = None
    code_context: CodeContext | None = None
    hypothesis: Hypothesis | None = None
    critic_report: CriticReport | None = None
    rca_doc: RCADoc | None = None

    # Stage tracking
    stages: list[StageRecord] = Field(default_factory=list)
    current_stage: str = ""

    # Budget tracking
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0
    budget_exceeded: bool = Field(
        default=False,
        description="True if any budget limit was hit. RCA is marked incomplete.",
    )
    budget_exceeded_reason: str = ""

    # Human review
    approved: bool = False
    human_notes: str = ""
