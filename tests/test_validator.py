"""Tests for trip validator."""

import json
from pathlib import Path

import pytest

from oncall_rca.stages.evidence.validator import (
    ValidationCheck,
    ValidationReport,
    _get_sms_hold_baggage,
    _get_sms_hold_fare,
    _get_sms_hold_fbc,
    _get_supplier_book_baggage,
    _get_supplier_book_fare,
    _get_supplier_book_fbc,
    _get_supplier_pq_fare,
    format_report,
    validate_trip,
)
from oncall_rca.tools.cache import TripCache
from oncall_rca.tools.log_api import parse_payload

FIXTURE_DIR = Path("evals/golden_incidents/260802431929")


class TestSmsHoldExtraction:
    @pytest.fixture
    def sms_hold(self):
        path = FIXTURE_DIR / "payloads" / "NEW-SMS-SMS_HOLD-10.36.39.9-1785662097680-3611-res.gz.txt"
        if not path.exists():
            pytest.skip("fixture not available")
        return json.loads(path.read_text())

    def test_fare(self, sms_hold) -> None:
        fare = _get_sms_hold_fare(sms_hold)
        assert fare == "1253.28"

    def test_fbc(self, sms_hold) -> None:
        fbc = _get_sms_hold_fbc(sms_hold)
        assert fbc == "P"

    def test_baggage_included(self, sms_hold) -> None:
        has_baggage = _get_sms_hold_baggage(sms_hold)
        assert has_baggage is True  # CHECK_IN_BAGGAGE in inclusions


class TestSupplierBookExtraction:
    @pytest.fixture
    def supplier_book(self):
        path = FIXTURE_DIR / "payloads" / "NEW-SMS-SUPPLIER_BOOK-10.36.3.110-1785662214823-4582-res.gz.txt"
        if not path.exists():
            pytest.skip("fixture not available")
        return parse_payload(path.read_bytes())

    def test_fare(self, supplier_book) -> None:
        fare = _get_supplier_book_fare(supplier_book)
        assert fare == "1253.28"

    def test_fbc(self, supplier_book) -> None:
        fbc = _get_supplier_book_fbc(supplier_book)
        assert fbc == "P"

    def test_baggage(self, supplier_book) -> None:
        bags = _get_supplier_book_baggage(supplier_book)
        assert len(bags) >= 1
        assert any("30 Kg" in b for b in bags)


class TestSupplierPriceQuoteExtraction:
    def test_fare(self) -> None:
        path = FIXTURE_DIR / "payloads" / "NEW-SMS-SUPPLIER_PRICE_QUOTE-10.36.38.243-1785659819453-574-res.gz.txt"
        if not path.exists():
            pytest.skip("fixture not available")
        parsed = parse_payload(path.read_bytes())
        fare = _get_supplier_pq_fare(parsed)
        assert fare == "1253.28"


class TestValidateGoldenTrip:
    """Validate the golden fixture (healthy booking — all checks should pass)."""

    @pytest.fixture
    def report(self, tmp_path: Path):
        if not FIXTURE_DIR.exists():
            pytest.skip("fixture not available")

        raw = json.loads((FIXTURE_DIR / "trip_index.json").read_text())

        # Set up cache with payload files
        cache = TripCache(tmp_path / "cache")
        cache.write_trip_index("260802431929", raw["data"])

        # Copy payload files into cache
        payloads_dir = FIXTURE_DIR / "payloads"
        for f in payloads_dir.iterdir():
            if f.is_file():
                # Reverse the .txt extension added during fixture capture
                orig_name = f.stem  # removes .txt
                cache.write_file("260802431929", orig_name, f.read_bytes())

        return validate_trip("260802431929", raw["data"], cache)

    def test_has_journeys(self, report: ValidationReport) -> None:
        assert len(report.journeys) == 2

    def test_hold_status_passes(self, report: ValidationReport) -> None:
        for jv in report.journeys:
            hold_checks = [c for c in jv.checks if c.name == "Hold status"]
            if hold_checks:
                assert hold_checks[0].status == "pass"

    def test_no_critical_failures(self, report: ValidationReport) -> None:
        # Golden trip is healthy — no baggage/fare failures expected
        for jv in report.journeys:
            for check in jv.checks:
                if check.status == "fail":
                    # Allow booking status fail for retry cases
                    assert check.name in ("Booking status",), \
                        f"Unexpected failure: {check.name}: {check.detail}"


class TestFormatReport:
    def test_format(self) -> None:
        report = ValidationReport(trip_id="123456789012")
        output = format_report(report)
        assert "123456789012" in output
        assert "Validation Report" in output
