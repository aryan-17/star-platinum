"""Tests for timeline CLI — snapshot test against golden fixture."""

import json
from pathlib import Path

import pytest

from oncall_rca.entrypoints.timeline_cli import build_timeline


@pytest.fixture
def cache_with_fixture(tmp_path: Path) -> Path:
    """Pre-populate a cache dir from the golden fixture."""
    fixture_path = Path("evals/golden_incidents/260802431929/trip_index.json")
    if not fixture_path.exists():
        pytest.skip("fixture not available")

    raw = json.loads(fixture_path.read_text())
    trip_dir = tmp_path / "260802431929"
    trip_dir.mkdir()
    (trip_dir / "trip_index.json").write_text(json.dumps(raw["data"]))
    return tmp_path


class TestBuildTimeline:
    def test_offline_from_cache(self, cache_with_fixture: Path) -> None:
        timeline = build_timeline(
            "260802431929", offline=True, cache_dir=cache_with_fixture
        )
        assert timeline["trip_id"] == "260802431929"

    def test_two_journeys(self, cache_with_fixture: Path) -> None:
        timeline = build_timeline(
            "260802431929", offline=True, cache_dir=cache_with_fixture
        )
        assert len(timeline["journeys"]) == 2

    def test_journey_routes(self, cache_with_fixture: Path) -> None:
        timeline = build_timeline(
            "260802431929", offline=True, cache_dir=cache_with_fixture
        )
        routes = {j["route"] for j in timeline["journeys"]}
        assert "COK→CAI" in routes
        assert "CAI→COK" in routes

    def test_air_arabia_detected(self, cache_with_fixture: Path) -> None:
        timeline = build_timeline(
            "260802431929", offline=True, cache_dir=cache_with_fixture
        )
        for j in timeline["journeys"]:
            assert j["supplier"] == "air_arabia"

    def test_supplier_book_retry_flagged(self, cache_with_fixture: Path) -> None:
        timeline = build_timeline(
            "260802431929", offline=True, cache_dir=cache_with_fixture
        )
        # COK→CAI journey should have a SUPPLIER_BOOK retry
        cok_cai = [j for j in timeline["journeys"] if j["route"] == "COK→CAI"][0]
        retry_calls = [
            t for t in cok_cai["timeline"]
            if t["api"] == "SUPPLIER_BOOK" and "RETRY" in t.get("flags", [])
        ]
        assert len(retry_calls) >= 1

    def test_anomalies_detected(self, cache_with_fixture: Path) -> None:
        timeline = build_timeline(
            "260802431929", offline=True, cache_dir=cache_with_fixture
        )
        assert len(timeline["anomalies"]) > 0
        # Should flag the duplicate RATE_RULE_EVALUATION
        dup_anomalies = [a for a in timeline["anomalies"] if "duplicate" in a]
        assert len(dup_anomalies) >= 1

    def test_offline_identical_output(self, cache_with_fixture: Path) -> None:
        """Two offline runs produce identical output."""
        t1 = build_timeline("260802431929", offline=True, cache_dir=cache_with_fixture)
        t2 = build_timeline("260802431929", offline=True, cache_dir=cache_with_fixture)
        assert json.dumps(t1, sort_keys=True) == json.dumps(t2, sort_keys=True)

    def test_file_inventory_count(self, cache_with_fixture: Path) -> None:
        timeline = build_timeline(
            "260802431929", offline=True, cache_dir=cache_with_fixture
        )
        assert timeline["total_files"] == 377
