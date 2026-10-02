"""End-to-end RCA pipeline — wires all stages together.

One command: trip ID → RCA document.
Checkpoints after each stage for resumability.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from oncall_rca.config.settings import Settings
from oncall_rca.llm.client import LLMClient
from oncall_rca.schemas.code_context import CodeContext
from oncall_rca.schemas.evidence import EvidencePack
from oncall_rca.schemas.hypothesis import Confidence, CriticReport, Hypothesis
from oncall_rca.schemas.incident import Incident, IncidentType
from oncall_rca.schemas.run_state import RunState, StageRecord, StageStatus
from oncall_rca.stages.critic.critic import critique
from oncall_rca.stages.evidence.engine import build_evidence_pack
from oncall_rca.stages.investigator.investigator import investigate
from oncall_rca.stages.rca_writer.writer import build_rca_doc, render_rca_markdown, save_rca
from oncall_rca.tools.cache import TripCache
from oncall_rca.tools.log_api import fetch_trip_index


def run_pipeline(
    trip_id: str,
    *,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    incident: Incident | None = None,
    offline: bool = False,
    cache_dir: Path | None = None,
    output_dir: Path | None = None,
) -> RunState:
    """Run the full RCA pipeline for a trip.

    Args:
        trip_id: 12-digit trip reference.
        settings: App settings (loaded from .env if None).
        llm: LLM client (created from settings if None).
        incident: Pre-built incident (created synthetically if None).
        offline: Read from cache only.
        cache_dir: Override cache directory.
        output_dir: Override RCA output directory.

    Returns:
        Final RunState with all stage outputs.
    """
    if settings is None:
        settings = Settings()
    if llm is None:
        from oncall_rca.llm.client import GroqClient
        llm = GroqClient.from_settings(settings)

    cache = TripCache(cache_dir or settings.output.cache_dir)
    out_dir = output_dir or settings.output.rca_dir

    run_id = str(uuid.uuid4())[:12]
    state = RunState(run_id=run_id, started_at=datetime.utcnow())

    # Stage 1: Incident (synthetic if not provided)
    state = _run_stage(state, "intake", lambda: _intake(trip_id, incident))

    # Stage 2: Evidence
    state = _run_stage(state, "evidence", lambda: _evidence(trip_id, settings, cache, offline))

    # Stage 3: Code Context (stub — needs actual repos)
    state = _run_stage(state, "code_context", lambda: CodeContext())

    # Stage 4: Investigator
    if state.evidence_pack is not None:
        state = _run_stage(
            state, "investigator",
            lambda: investigate(state.evidence_pack, state.code_context or CodeContext(), llm),  # type: ignore[arg-type]
        )

    # Stage 5: Critic
    if state.hypothesis is not None and state.evidence_pack is not None:
        state = _run_stage(
            state, "critic",
            lambda: critique(state.hypothesis, state.evidence_pack, llm),  # type: ignore[arg-type]
        )

    # Stage 6: RCA Writer
    if all([state.incident, state.evidence_pack, state.hypothesis, state.critic_report]):
        rca_doc = build_rca_doc(
            state.incident,  # type: ignore[arg-type]
            state.evidence_pack,  # type: ignore[arg-type]
            state.code_context or CodeContext(),
            state.hypothesis,  # type: ignore[arg-type]
            state.critic_report,  # type: ignore[arg-type]
        )
        state = state.model_copy(update={"rca_doc": rca_doc})

        # Save to disk
        rca_path = save_rca(rca_doc, out_dir)
        state = _add_stage_record(state, "rca_writer", StageStatus.COMPLETED)
    else:
        state = _add_stage_record(state, "rca_writer", StageStatus.SKIPPED)

    return state


def _intake(trip_id: str, incident: Incident | None) -> Incident:
    """Create or use incident."""
    if incident:
        return incident
    return Incident(
        message_id="manual",
        thread_id="manual",
        trip_ref=trip_id,
        incident_type=IncidentType.BAGGAGE_MISMATCH,
        reported_symptom="Manual pipeline run",
        received_at=datetime.utcnow(),
    )


def _evidence(
    trip_id: str,
    settings: Settings,
    cache: TripCache,
    offline: bool,
) -> EvidencePack:
    """Fetch trip data and build evidence pack."""
    if cache.has_trip_index(trip_id):
        raw_data = cache.read_trip_index(trip_id)
    elif offline:
        raise RuntimeError(f"Trip {trip_id} not in cache and offline=True")
    else:
        raw_data = fetch_trip_index(trip_id, settings)
        cache.write_trip_index(trip_id, raw_data)

    return build_evidence_pack(trip_id, raw_data, cache)


def _run_stage(state: RunState, stage_name: str, fn: Any) -> RunState:
    """Run a stage function and update state."""
    state = state.model_copy(update={"current_stage": stage_name})
    record = StageRecord(
        stage=stage_name,
        status=StageStatus.RUNNING,
        started_at=datetime.utcnow(),
    )

    try:
        result = fn()
        record = record.model_copy(update={
            "status": StageStatus.COMPLETED,
            "completed_at": datetime.utcnow(),
        })

        # Map result to the right state field
        field_map = {
            "intake": "incident",
            "evidence": "evidence_pack",
            "code_context": "code_context",
            "investigator": "hypothesis",
            "critic": "critic_report",
        }
        field = field_map.get(stage_name)
        updates: dict[str, Any] = {"stages": [*state.stages, record]}
        if field:
            updates[field] = result
        return state.model_copy(update=updates)

    except Exception as e:
        record = record.model_copy(update={
            "status": StageStatus.FAILED,
            "error": str(e),
            "completed_at": datetime.utcnow(),
        })
        return state.model_copy(update={"stages": [*state.stages, record]})


def _add_stage_record(state: RunState, stage: str, status: StageStatus) -> RunState:
    record = StageRecord(stage=stage, status=status, completed_at=datetime.utcnow())
    return state.model_copy(update={"stages": [*state.stages, record]})
