"""Tests for LangGraph StateGraph wiring."""

import json
from pathlib import Path

import pytest

from oncall_rca.llm.client import MockLLMClient
from oncall_rca.schemas.hypothesis import Confidence, CriticReport, Hypothesis
from oncall_rca.schemas.run_state import RunState, StageStatus


FIXTURE_DIR = Path("evals/golden_incidents/260802431929")


@pytest.fixture
def mock_llm() -> MockLLMClient:
    client = MockLLMClient()
    client.set_response(
        "Hypothesis",
        Hypothesis(claim="Test claim", explanation="Test explanation"),
    )
    client.set_response(
        "CriticReport",
        CriticReport(all_citations_valid=True, confidence=Confidence.HIGH),
    )
    return client


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    if not FIXTURE_DIR.exists():
        pytest.skip("fixture not available")
    raw = json.loads((FIXTURE_DIR / "trip_index.json").read_text())
    trip_dir = tmp_path / "cache" / "260802431929"
    trip_dir.mkdir(parents=True)
    (trip_dir / "trip_index.json").write_text(json.dumps(raw["data"]))
    return tmp_path / "cache"


def test_graph_produces_complete_state(
    mock_llm: MockLLMClient, cache_dir: Path, tmp_path: Path,
) -> None:
    from oncall_rca.config.settings import Settings
    from oncall_rca.tools.cache import TripCache
    from oncall_rca.workflow.graph import build_graph

    graph = build_graph(
        trip_id="260802431929",
        settings=Settings(),
        llm=mock_llm,
        cache=TripCache(cache_dir),
        out_dir=tmp_path / "rca",
        incident=None,
        offline=True,
    )
    initial = RunState(run_id="test-graph")
    result = graph.invoke(initial, config={"configurable": {"thread_id": "t1"}})
    assert result["incident"] is not None
    assert result["evidence_pack"] is not None


def test_graph_records_failed_stage(
    mock_llm: MockLLMClient, tmp_path: Path,
) -> None:
    """A node that fails must record FAILED status, not crash the graph."""
    from oncall_rca.config.settings import Settings
    from oncall_rca.tools.cache import TripCache
    from oncall_rca.workflow.graph import build_graph

    graph = build_graph(
        trip_id="000000000000",
        settings=Settings(),
        llm=mock_llm,
        cache=TripCache(tmp_path / "empty_cache"),
        out_dir=tmp_path / "rca",
        incident=None,
        offline=True,
    )
    initial = RunState(run_id="test-fail")
    result = graph.invoke(initial, config={"configurable": {"thread_id": "t2"}})
    stages = result.get("stages", [])
    failed = [s for s in stages if s.status == StageStatus.FAILED]
    assert len(failed) >= 1
