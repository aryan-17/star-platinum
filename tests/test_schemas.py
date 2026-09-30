"""Tests for schema validation — example objects must validate against all schemas."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from oncall_rca.schemas import (
    BaggageChainStep,
    CallNode,
    Citation,
    CitationCheck,
    CodeContext,
    CodeFile,
    Confidence,
    CriticReport,
    DivergencePoint,
    EvidenceFact,
    EvidencePack,
    FileRef,
    Hypothesis,
    Incident,
    IncidentType,
    Journey,
    PayloadFormat,
    RCADoc,
    RunState,
    Service,
    StageRecord,
    StageStatus,
    TimelineEntry,
    TripIndex,
)


class TestIncident:
    def test_valid_incident(self) -> None:
        incident = Incident(
            message_id="msg_123",
            thread_id="thread_456",
            trip_ref="260802431929",
            incident_type=IncidentType.BAGGAGE_MISMATCH,
            reported_symptom="User was shown 2 pieces baggage but none assigned after booking",
            received_at=datetime(2026, 9, 28, 10, 30, 0),
        )
        assert incident.trip_ref == "260802431929"
        assert incident.incident_type == IncidentType.BAGGAGE_MISMATCH

    def test_trip_ref_must_be_12_digits(self) -> None:
        with pytest.raises(ValidationError):
            Incident(
                message_id="msg_123",
                thread_id="thread_456",
                trip_ref="12345",  # too short
                incident_type=IncidentType.UNKNOWN,
                reported_symptom="test",
                received_at=datetime.now(),
            )

    def test_trip_ref_must_be_numeric(self) -> None:
        with pytest.raises(ValidationError):
            Incident(
                message_id="msg_123",
                thread_id="thread_456",
                trip_ref="abcdef123456",  # not numeric
                incident_type=IncidentType.UNKNOWN,
                reported_symptom="test",
                received_at=datetime.now(),
            )


class TestCallNode:
    def test_valid_call_node(self) -> None:
        node = CallNode(
            api="SMS_HOLD",
            api_type="NEW-SMS",
            service=Service.AIR_SMS_NEW,
            pod="air-sms-new-abc123",
            time=datetime(2026, 8, 2, 14, 44, 57),
            duration_ms=3200,
            identifier="corr-id-001",
        )
        assert node.service == Service.AIR_SMS_NEW
        assert not node.is_external
        assert not node.is_slow

    def test_invalid_duration_is_none(self) -> None:
        node = CallNode(api="ANCILLARY_OFFERS", api_type="NEW-SMS", duration_ms=None)
        assert node.duration_ms is None

    def test_slow_and_timeout_flags(self) -> None:
        node = CallNode(api="SUPPLIER_BOOK", api_type="NEW-SMS", is_slow=True, is_timeout=True)
        assert node.is_slow
        assert node.is_timeout


class TestTripIndex:
    def test_valid_trip_index(self) -> None:
        trip = TripIndex(
            trip_id="260802431929",
            itineraries=["itin_1", "itin_2"],
            journeys=[
                Journey(
                    journey_index=0,
                    origin="COK",
                    destination="CAI",
                    supplier="air_arabia",
                ),
                Journey(
                    journey_index=1,
                    origin="CAI",
                    destination="COK",
                    supplier="air_arabia",
                ),
            ],
        )
        assert len(trip.journeys) == 2
        assert trip.journeys[0].origin == "COK"


class TestEvidence:
    def test_evidence_fact(self) -> None:
        fact = EvidenceFact(
            fact_id="j0_pax0_ss1_checkin_bag",
            journey_index=0,
            step=BaggageChainStep.SS1_DISPLAYED,
            label="SS1 check-in baggage",
            value="2 pieces",
            citation=Citation(
                source_file="SUPPLY_CORE-SINGLE_SOLUTION_SEARCH-123-res.gz",
                field_path="fareFamilyDTO[0].fareBenefits[0].qty",
                excerpt="2",
            ),
        )
        assert fact.step == BaggageChainStep.SS1_DISPLAYED

    def test_evidence_pack_no_divergence(self) -> None:
        pack = EvidencePack(
            trip_index=TripIndex(trip_id="260802431929"),
            divergence_point=None,
        )
        assert pack.divergence_point is None

    def test_evidence_pack_with_divergence(self) -> None:
        pack = EvidencePack(
            trip_index=TripIndex(trip_id="260802431929"),
            divergence_point=DivergencePoint(
                journey_index=0,
                step=BaggageChainStep.SIS_HOLD_REQUEST,
                expected_value="2 pieces",
                actual_value="0 pieces",
                citation=Citation(source_file="HOLD-req.gz", field_path="fareDetails.baggage"),
                divergence_class="SIS-HOLD FBC/fare != SS1",
            ),
        )
        assert pack.divergence_point is not None
        assert pack.divergence_point.step == BaggageChainStep.SIS_HOLD_REQUEST


class TestHypothesis:
    def test_valid_hypothesis(self) -> None:
        h = Hypothesis(
            claim="HoldMainWorkflow dropped baggage from the hold request",
            explanation="The fare benefits from SS1 were not propagated to the hold request builder.",
            supporting_citations=[
                Citation(
                    source_file="HOLD-req.gz",
                    field_path="bookedPromise.fareDetails",
                    excerpt="baggage field missing",
                ),
            ],
            affected_code=[
                CodeFile(repo="supply-core-new", path="src/hold/HoldMainWorkflow.java"),
            ],
        )
        assert len(h.supporting_citations) == 1

    def test_critic_report(self) -> None:
        report = CriticReport(
            citation_checks=[
                CitationCheck(
                    citation=Citation(source_file="test.gz"),
                    exists=True,
                    says_what_claimed=True,
                ),
            ],
            all_citations_valid=True,
            confidence=Confidence.HIGH,
            rubric_scores={
                "citation_coverage": True,
                "alternatives_ruled_out": True,
                "timeline_consistent": True,
                "all_symptoms_explained": True,
                "reproduction_path_exists": False,
            },
        )
        assert report.confidence == Confidence.HIGH


class TestRCADoc:
    def test_minimal_rca(self) -> None:
        doc = RCADoc(
            trip_id="260802431929",
            confidence=Confidence.MEDIUM,
            summary="Baggage dropped in hold request due to missing fare benefit propagation.",
            reported_symptom="2 pieces shown, none assigned after booking",
        )
        assert doc.trip_id == "260802431929"


class TestRunState:
    def test_fresh_run_state(self) -> None:
        state = RunState(run_id="run-001")
        assert state.incident is None
        assert state.trip_index is None
        assert state.evidence_pack is None
        assert state.total_tokens_used == 0
        assert not state.budget_exceeded
        assert not state.approved

    def test_run_state_with_stage_records(self) -> None:
        state = RunState(
            run_id="run-002",
            stages=[
                StageRecord(stage="intake", status=StageStatus.COMPLETED),
                StageRecord(stage="preflight", status=StageStatus.RUNNING),
            ],
            current_stage="preflight",
        )
        assert len(state.stages) == 2
        assert state.stages[1].status == StageStatus.RUNNING
