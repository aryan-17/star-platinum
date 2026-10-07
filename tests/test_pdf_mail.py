"""Tests for PDF mail intake."""

from pathlib import Path
from unittest.mock import patch

import pytest

from oncall_rca.schemas.incident import IncidentType
from oncall_rca.stages.intake.pdf_mail import (
    _classify_incident,
    _extract_symptom,
    _extract_trip_ref,
    extract_from_pdf,
)


SAMPLE_PDF = Path("/Users/ctme/Downloads/Flyin.com Mail - Re_ 5501260967375.pdf")


class TestExtractTripRef:
    def test_from_filename(self) -> None:
        ref = _extract_trip_ref("some text", "Flyin.com Mail - Re_ 5501260967375.pdf")
        assert ref == "5501260967375"

    def test_from_text(self) -> None:
        ref = _extract_trip_ref("Re: 260802431929\nDear Team", "unknown.pdf")
        assert ref == "260802431929"

    def test_13_digit(self) -> None:
        ref = _extract_trip_ref("Trip 5501260967375 needs review", "mail.pdf")
        assert ref == "5501260967375"

    def test_no_match_raises(self) -> None:
        with pytest.raises(ValueError, match="No trip reference"):
            _extract_trip_ref("no numbers here", "mail.pdf")


class TestClassifyIncident:
    def test_baggage_mismatch(self) -> None:
        text = "The booking shows checked baggage allowance of 25 kg but no baggage in Amadeus"
        assert _classify_incident(text) == IncidentType.BAGGAGE_MISMATCH

    def test_unknown(self) -> None:
        text = "Customer wants to change flight date"
        assert _classify_incident(text) == IncidentType.UNKNOWN


class TestExtractSymptom:
    def test_extracts_core_message(self) -> None:
        text = (
            "Dear Team,\n"
            "Kindly could you check and approve a refund\n"
            "The booking shows a checked baggage allowance of 25 kg in HQ, "
            "while no baggage allowance is reflected in Amadeus.\n"
            "customer paid at airport 545.65 EUR\n"
            "\n"
            "Regards,\n"
            "Fulfillment"
        )
        symptom = _extract_symptom(text)
        assert "25 kg" in symptom
        assert "no baggage" in symptom


class TestRealPDF:
    """Integration test against the actual PDF file."""

    def test_extract_from_real_pdf(self) -> None:
        if not SAMPLE_PDF.exists():
            pytest.skip("sample PDF not available")

        incident = extract_from_pdf(SAMPLE_PDF)
        assert incident.trip_ref == "5501260967375"
        assert incident.incident_type == IncidentType.BAGGAGE_MISMATCH
        assert len(incident.reported_symptom) > 20
        assert "baggage" in incident.reported_symptom.lower() or "25 kg" in incident.reported_symptom
