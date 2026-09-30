"""Shared fixtures for the test suite."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from oncall_rca.llm.client import MockLLMClient
from oncall_rca.schemas import (
    BaggageChainStep,
    CallNode,
    Citation,
    CodeContext,
    CodeFile,
    Confidence,
    DivergencePoint,
    EvidenceFact,
    EvidencePack,
    Hypothesis,
    Incident,
    IncidentType,
    Journey,
    RCADoc,
    RunState,
    Service,
    TripIndex,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_DIR = Path(__file__).parent.parent / "evals" / "golden_incidents"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def golden_dir() -> Path:
    return GOLDEN_DIR


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient()


@pytest.fixture
def sample_incident() -> Incident:
    return Incident(
        message_id="msg_sample_001",
        thread_id="thread_sample_001",
        trip_ref="260802431929",
        incident_type=IncidentType.BAGGAGE_MISMATCH,
        reported_symptom="User was shown 2 pieces baggage but none assigned after booking",
        received_at=datetime(2026, 8, 2, 15, 0, 0),
    )


@pytest.fixture
def sample_trip_index() -> TripIndex:
    return TripIndex(
        trip_id="260802431929",
        itineraries=["itin_001", "itin_002"],
        trips=["trip_001"],
        channel="web",
        pax_count=1,
        journeys=[
            Journey(
                journey_index=0,
                origin="COK",
                destination="CAI",
                supplier="air_arabia",
                supplier_detected_by="url:airarabia.com",
            ),
            Journey(
                journey_index=1,
                origin="CAI",
                destination="COK",
                supplier="air_arabia",
                supplier_detected_by="url:airarabia.com",
            ),
        ],
    )


@pytest.fixture
def sample_evidence_pack(sample_trip_index: TripIndex) -> EvidencePack:
    """Evidence pack for the negative control (no divergence)."""
    return EvidencePack(
        trip_index=sample_trip_index,
        divergence_point=None,
        supplier="air_arabia",
        red_herrings=["journeyFareSummary.passengerBaggageDetails is fare metadata, not booked baggage"],
    )


@pytest.fixture
def sample_run_state(sample_incident: Incident) -> RunState:
    return RunState(
        run_id="test-run-001",
        incident=sample_incident,
    )
