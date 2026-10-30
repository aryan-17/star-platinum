"""End-to-end RCA pipeline — wires all stages together.

One command: trip ID → RCA document.
Delegates to a LangGraph StateGraph with MemorySaver checkpointing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from oncall_rca.config.settings import Settings
from oncall_rca.llm.client import LLMClient
from oncall_rca.schemas.incident import Incident
from oncall_rca.schemas.run_state import RunState
from oncall_rca.tools.cache import TripCache
from oncall_rca.workflow.graph import build_graph


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

    graph = build_graph(
        trip_id=trip_id,
        settings=settings,
        llm=llm,
        cache=cache,
        out_dir=out_dir,
        incident=incident,
        offline=offline,
    )

    result = graph.invoke(
        RunState(run_id=run_id, started_at=datetime.now(tz=UTC)),
        config={"configurable": {"thread_id": run_id}},
    )

    # LangGraph returns a dict — convert back to RunState
    if isinstance(result, dict):
        return RunState.model_validate(result)
    return result
