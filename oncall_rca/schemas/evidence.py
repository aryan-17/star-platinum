"""Evidence schemas — output of the Evidence Engine stage."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from oncall_rca.schemas.trip_index import CallNode, Journey, TripIndex


class BaggageChainStep(str, Enum):
    """Steps in the baggage mismatch playbook chain (§8.3)."""

    SS1_DISPLAYED = "ss1_displayed"
    SS1_CONSISTENCY = "ss1_consistency"
    SIS_HOLD_REQUEST = "sis_hold_request"
    SUPPLIER_BAGGAGE_HOLD = "supplier_baggage_hold"
    SUPPLIER_PRICE_HOLD = "supplier_price_hold"
    HOLD_CORE_RESPONSE = "hold_core_response"
    BOOK_RESPONSE = "book_response"
    SUPPLIER_BOOK = "supplier_book"
    SUPPLIER_BOOK_RETRY = "supplier_book_retry"
    PURCHASED_ANCILLARY = "purchased_ancillary"


class Citation(BaseModel):
    """A pointer to evidence in a log file or code file."""

    source_file: str = Field(description="Log file name or code file path")
    field_path: str = Field(
        default="",
        description="JSON path, XPath, or line range pointing to the value",
    )
    excerpt: str = Field(
        default="",
        description="Short text excerpt of the cited value",
    )


class EvidenceFact(BaseModel):
    """A single extracted fact with its source citation.

    Produced by: extractors.
    Consumed by: divergence finder, Investigator, Critic, RCA Writer.
    """

    fact_id: str = Field(description="Unique ID for drill-down, e.g. 'j0_pax0_ss1_checkin_bag'")
    journey_index: int = Field(description="Which journey this fact belongs to")
    passenger_index: int = Field(default=0, description="Which passenger (0-based)")
    step: BaggageChainStep
    label: str = Field(description="Human-readable label, e.g. 'SS1 check-in baggage'")
    value: str = Field(description="Extracted value, e.g. '2 pieces' or '23kg'")
    citation: Citation


class DivergencePoint(BaseModel):
    """Where the invariant first broke in the playbook chain."""

    journey_index: int
    passenger_index: int = 0
    step: BaggageChainStep
    expected_value: str = Field(description="What the value should have been")
    actual_value: str = Field(description="What was found instead")
    citation: Citation
    divergence_class: str = Field(
        default="",
        description="From §8.4: e.g. 'SIS-HOLD FBC/fare != SS1'",
    )


class EvidencePack(BaseModel):
    """Complete evidence package for a trip incident.

    Produced by: Evidence Engine stage.
    Consumed by: Investigator, Critic, RCA Writer.
    """

    trip_index: TripIndex
    facts: list[EvidenceFact] = Field(default_factory=list)
    divergence_point: DivergencePoint | None = Field(
        default=None,
        description="None means no divergence found in the core chain",
    )
    anomalies: list[str] = Field(
        default_factory=list,
        description="Non-divergence anomalies worth noting",
    )
    red_herrings: list[str] = Field(
        default_factory=list,
        description="Known red herrings flagged for the Investigator",
    )
    supplier: str = Field(default="", description="Detected supplier for the primary journey")
    facts_by_journey: dict[int, list[EvidenceFact]] = Field(
        default_factory=dict,
        description="Facts grouped by journey index for quick access",
    )
