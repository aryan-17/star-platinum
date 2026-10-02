"""Tests for end-to-end pipeline — using MockLLMClient."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from oncall_rca.llm.client import MockLLMClient
from oncall_rca.schemas.hypothesis import Confidence, CriticReport, Hypothesis
from oncall_rca.schemas.incident import Incident, IncidentType
from oncall_rca.schemas.run_state import StageStatus
from oncall_rca.stages.rca_writer.writer import render_rca_markdown, build_rca_doc, save_rca
from oncall_rca.schemas.code_context import CodeContext
from oncall_rca.schemas.evidence import EvidencePack
from oncall_rca.schemas.trip_index import TripIndex
from oncall_rca.workflow.pipeline import run_pipeline


FIXTURE_DIR = Path("evals/golden_incidents/260802431929")


@pytest.fixture
def mock_llm() -> MockLLMClient:
    client = MockLLMClient()
    client.set_response("Hypothesis", Hypothesis(
        claim="No divergence in core baggage chain",
        explanation="All steps are consistent. Both journeys booked successfully with matching fares.",
        proposed_fix="No fix needed for this trip.",
    ))
    client.set_response("CriticReport", CriticReport(
        all_citations_valid=True,
        confidence=Confidence.HIGH,
        rubric_scores={
            "citation_coverage": True,
            "alternatives_ruled_out": True,
            "timeline_consistent": True,
            "all_symptoms_explained": True,
            "reproduction_path_exists": False,
        },
    ))
    return client


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Pre-populate cache from golden fixture."""
    if not FIXTURE_DIR.exists():
        pytest.skip("fixture not available")
    raw = json.loads((FIXTURE_DIR / "trip_index.json").read_text())
    trip_dir = tmp_path / "cache" / "260802431929"
    trip_dir.mkdir(parents=True)
    (trip_dir / "trip_index.json").write_text(json.dumps(raw["data"]))
    return tmp_path / "cache"


class TestPipeline:
    def test_full_pipeline_mock(self, mock_llm: MockLLMClient, cache_dir: Path, tmp_path: Path) -> None:
        output_dir = tmp_path / "rca_output"

        state = run_pipeline(
            "260802431929",
            llm=mock_llm,
            offline=True,
            cache_dir=cache_dir,
            output_dir=output_dir,
        )

        assert state.run_id != ""
        assert state.incident is not None
        assert state.evidence_pack is not None
        assert state.hypothesis is not None
        assert state.critic_report is not None
        assert state.rca_doc is not None

    def test_all_stages_completed(self, mock_llm: MockLLMClient, cache_dir: Path, tmp_path: Path) -> None:
        state = run_pipeline(
            "260802431929",
            llm=mock_llm,
            offline=True,
            cache_dir=cache_dir,
            output_dir=tmp_path / "rca",
        )

        completed = [s for s in state.stages if s.status == StageStatus.COMPLETED]
        assert len(completed) >= 5  # intake, evidence, code_context, investigator, critic

    def test_rca_saved_to_disk(self, mock_llm: MockLLMClient, cache_dir: Path, tmp_path: Path) -> None:
        output_dir = tmp_path / "rca_output"
        state = run_pipeline(
            "260802431929",
            llm=mock_llm,
            offline=True,
            cache_dir=cache_dir,
            output_dir=output_dir,
        )

        rca_path = output_dir / "260802431929" / "RCA.md"
        assert rca_path.exists()
        content = rca_path.read_text()
        assert "# RCA — Trip 260802431929" in content
        assert "Confidence:" in content

    def test_rca_json_saved(self, mock_llm: MockLLMClient, cache_dir: Path, tmp_path: Path) -> None:
        output_dir = tmp_path / "rca_output"
        run_pipeline(
            "260802431929",
            llm=mock_llm,
            offline=True,
            cache_dir=cache_dir,
            output_dir=output_dir,
        )

        json_path = output_dir / "260802431929" / "rca.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["trip_id"] == "260802431929"


class TestRCAWriter:
    def test_render_markdown(self) -> None:
        from oncall_rca.schemas.rca_doc import RCADoc

        doc = RCADoc(
            trip_id="260802431929",
            confidence=Confidence.HIGH,
            summary="No divergence found",
            reported_symptom="Test symptom",
            itinerary_ids=["NIX123"],
            journeys=["COK→CAI (air_arabia)"],
        )
        md = render_rca_markdown(doc)
        assert "# RCA — Trip 260802431929" in md
        assert "HIGH" in md
        assert "Test symptom" in md

    def test_save_creates_files(self, tmp_path: Path) -> None:
        from oncall_rca.schemas.rca_doc import RCADoc

        doc = RCADoc(
            trip_id="999999999999",
            confidence=Confidence.MEDIUM,
            summary="Test",
            reported_symptom="Test",
        )
        rca_path = save_rca(doc, tmp_path)
        assert rca_path.exists()
        assert (tmp_path / "999999999999" / "rca.json").exists()
        assert (tmp_path / "999999999999" / "evidence").is_dir()
