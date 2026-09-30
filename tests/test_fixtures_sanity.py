"""Sanity tests — shared fixtures produce valid schema instances."""

from oncall_rca.schemas import (
    EvidencePack,
    Incident,
    IncidentType,
    RunState,
    TripIndex,
)


def test_sample_incident(sample_incident: Incident) -> None:
    assert sample_incident.trip_ref == "260802431929"
    assert sample_incident.incident_type == IncidentType.BAGGAGE_MISMATCH


def test_sample_trip_index(sample_trip_index: TripIndex) -> None:
    assert len(sample_trip_index.journeys) == 2
    assert sample_trip_index.journeys[0].origin == "COK"
    assert sample_trip_index.journeys[1].destination == "COK"


def test_sample_evidence_pack_negative_control(sample_evidence_pack: EvidencePack) -> None:
    assert sample_evidence_pack.divergence_point is None
    assert sample_evidence_pack.supplier == "air_arabia"


def test_sample_run_state(sample_run_state: RunState) -> None:
    assert sample_run_state.run_id == "test-run-001"
    assert sample_run_state.incident is not None
    assert not sample_run_state.budget_exceeded
