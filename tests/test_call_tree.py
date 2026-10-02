"""Tests for call tree, journey split, and supplier detection."""

import json
from pathlib import Path

import pytest

from oncall_rca.stages.evidence.call_tree import (
    _extract_route,
    _domain_to_supplier,
    build_call_tree,
    split_journeys,
    detect_supplier,
)
from oncall_rca.stages.evidence.index_normaliser import normalise_trip_index


class TestExtractRoute:
    def test_sms_book_url(self) -> None:
        url = "http://air-sms-new4book.gcp-cltp.me/v2/journey/book/AIR_ARABIA__COK__CAI__3L__12"
        route = _extract_route(url)
        assert route is not None
        assert route["origin"] == "COK"
        assert route["destination"] == "CAI"
        assert route["carrier"] == "3L"

    def test_reverse_journey(self) -> None:
        url = "http://air-sms-new4book.gcp-cltp.me/v2/journey/book/AIR_ARABIA__CAI__COK__G9__62"
        route = _extract_route(url)
        assert route is not None
        assert route["origin"] == "CAI"
        assert route["destination"] == "COK"
        assert route["carrier"] == "G9"

    def test_no_route(self) -> None:
        assert _extract_route("/some/other/url") is None


class TestDomainToSupplier:
    def test_airarabia(self) -> None:
        assert _domain_to_supplier("https://reservations.airarabia.com/ws") == "air_arabia"

    def test_accelaero(self) -> None:
        assert _domain_to_supplier("https://search.accelaero.com/api") == "air_arabia"

    def test_unknown(self) -> None:
        assert _domain_to_supplier("https://unknown.example.com") == ""


class TestRealTripJourneySplit:
    """Test journey split against the real golden fixture."""

    @pytest.fixture
    def trip_index(self):
        path = Path("evals/golden_incidents/260802431929/trip_index.json")
        if not path.exists():
            pytest.skip("fixture not available")
        raw = json.loads(path.read_text())
        idx = normalise_trip_index(raw["data"])
        idx = build_call_tree(idx)
        idx = split_journeys(idx)
        idx = detect_supplier(idx)
        return idx

    def test_two_journeys(self, trip_index) -> None:
        assert len(trip_index.journeys) == 2

    def test_journey_routes(self, trip_index) -> None:
        origins = {j.origin for j in trip_index.journeys}
        dests = {j.destination for j in trip_index.journeys}
        assert "COK" in origins
        assert "CAI" in origins
        assert "COK" in dests
        assert "CAI" in dests

    def test_supplier_detected(self, trip_index) -> None:
        for j in trip_index.journeys:
            assert j.supplier == "air_arabia", f"Journey {j.journey_index}: supplier={j.supplier}"

    def test_journeys_have_calls(self, trip_index) -> None:
        for j in trip_index.journeys:
            assert len(j.calls) > 0, f"Journey {j.journey_index} has no calls"

    def test_call_tree_has_children(self, trip_index) -> None:
        calls_with_children = [c for c in trip_index.all_calls if c.children]
        assert len(calls_with_children) > 0
