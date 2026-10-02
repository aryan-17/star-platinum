"""Tests for index normaliser — unit tests for each quirk + real fixture test."""

import json
from pathlib import Path

import pytest

from oncall_rca.schemas.trip_index import Service
from oncall_rca.stages.evidence.index_normaliser import (
    _extract_file_inventory,
    _flag_retries,
    _parse_duration,
    _parse_time,
    _pod_to_service,
    normalise_trip_index,
)


class TestParseDuration:
    def test_valid(self) -> None:
        assert _parse_duration("66") == 66
        assert _parse_duration("3200") == 3200

    def test_negative_epoch(self) -> None:
        assert _parse_duration("-1785659819433") is None

    def test_empty(self) -> None:
        assert _parse_duration("") is None
        assert _parse_duration(None) is None

    def test_absurdly_large(self) -> None:
        assert _parse_duration("999999") is None  # > 10 min


class TestParseTime:
    def test_with_millis(self) -> None:
        t = _parse_time("2026-08-02 14:06:56.186")
        assert t is not None
        assert t.year == 2026
        assert t.microsecond == 186000

    def test_without_millis(self) -> None:
        t = _parse_time("2026-08-02 14:06:56")
        assert t is not None

    def test_empty(self) -> None:
        assert _parse_time("") is None

    def test_invalid(self) -> None:
        assert _parse_time("not-a-date") is None


class TestPodToService:
    def test_air_sms_new(self) -> None:
        assert _pod_to_service("air-sms-new-abc123") == Service.AIR_SMS_NEW
        assert _pod_to_service("air-sms-new4book-xyz") == Service.AIR_SMS_NEW

    def test_air_sms_not_new(self) -> None:
        assert _pod_to_service("air-sms-pod1") == Service.AIR_SMS

    def test_supply_core(self) -> None:
        assert _pod_to_service("supply-core-4hold-abc") == Service.SUPPLY_CORE_NEW
        assert _pod_to_service("supply-core-xyz") == Service.SUPPLY_CORE_NEW

    def test_longest_prefix_wins(self) -> None:
        # air-sms-new must match before air-sms
        assert _pod_to_service("air-sms-new-pod") == Service.AIR_SMS_NEW

    def test_unknown_pod(self) -> None:
        assert _pod_to_service("itinex-577bfd9ff8-tfcpr") == Service.OTHER

    def test_empty(self) -> None:
        assert _pod_to_service("") == Service.OTHER


class TestExtractFileInventory:
    def test_nested_dict(self) -> None:
        files_list = [{"files": ["a.gz", "b.gz", "c.gz"]}]
        result = _extract_file_inventory(files_list)
        assert result == ["a.gz", "b.gz", "c.gz"]

    def test_flat_strings(self) -> None:
        result = _extract_file_inventory(["a.gz", "b.gz"])
        assert result == ["a.gz", "b.gz"]

    def test_empty(self) -> None:
        assert _extract_file_inventory([]) == []


class TestNormaliseRealTrip:
    """Normalise the real golden fixture and verify key properties."""

    @pytest.fixture
    def trip_index(self):
        path = Path("evals/golden_incidents/260802431929/trip_index.json")
        if not path.exists():
            pytest.skip("fixture not available")
        raw = json.loads(path.read_text())
        return normalise_trip_index(raw["data"])

    def test_trip_id(self, trip_index) -> None:
        assert trip_index.trip_id == "260802431929"

    def test_has_calls(self, trip_index) -> None:
        assert len(trip_index.all_calls) > 50

    def test_no_invalid_durations(self, trip_index) -> None:
        for call in trip_index.all_calls:
            if call.duration_ms is not None:
                assert call.duration_ms >= 0, f"Negative duration: {call.api} = {call.duration_ms}"
                assert call.duration_ms <= 600_000, f"Absurd duration: {call.api} = {call.duration_ms}"

    def test_no_duplicates(self, trip_index) -> None:
        keys = set()
        for call in trip_index.all_calls:
            key = f"{call.api}|{call.req_file.file_name if call.req_file else ''}|{call.res_file.file_name if call.res_file else ''}"
            assert key not in keys, f"Duplicate: {key}"
            keys.add(key)

    def test_file_inventory(self, trip_index) -> None:
        assert len(trip_index.file_inventory) > 300  # plan says ~377

    def test_pod_service_mapping(self, trip_index) -> None:
        services = {call.service for call in trip_index.all_calls if call.pod}
        # Should have at least some mapped services
        assert Service.OTHER not in services or len(services) > 1

    def test_itineraries(self, trip_index) -> None:
        assert len(trip_index.itineraries) >= 1
        assert trip_index.itineraries[0].startswith("NIX")
