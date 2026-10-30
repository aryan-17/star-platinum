"""LangGraph StateGraph wiring — replaces sequential pipeline with a compiled graph.

Each stage is a graph node (closure over settings/llm/cache). Nodes return
partial state dicts. MemorySaver checkpointer enables resume-after-crash.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from oncall_rca.llm.client import LLMClient
from oncall_rca.routing import route_by_confidence
from oncall_rca.schemas.code_context import CodeContext
from oncall_rca.schemas.evidence import EvidencePack
from oncall_rca.schemas.hypothesis import CriticReport, Hypothesis
from oncall_rca.schemas.incident import Incident, IncidentType
from oncall_rca.schemas.run_state import RunState, StageRecord, StageStatus
from oncall_rca.stages.critic.critic import critique
from oncall_rca.stages.investigator.investigator import investigate_journey
from oncall_rca.stages.rca_writer.writer import build_rca_doc, save_rca
from oncall_rca.tools.cache import TripCache


def build_graph(
    *,
    trip_id: str,
    settings: Any,
    llm: LLMClient,
    cache: TripCache,
    out_dir: Path,
    incident: Incident | None = None,
    offline: bool = False,
) -> CompiledStateGraph:
    """Build a compiled LangGraph StateGraph for the RCA pipeline.

    Each node is a closure capturing the external dependencies (settings,
    llm, cache, etc).  Nodes return partial state dicts.
    """

    # --- Node functions (closures) ---

    def intake_node(state: RunState) -> dict[str, Any]:
        record = StageRecord(
            stage="intake",
            status=StageStatus.RUNNING,
            started_at=datetime.now(tz=UTC),
        )
        try:
            if incident is not None:
                inc = incident
            else:
                inc = Incident(
                    message_id="manual",
                    thread_id="manual",
                    trip_ref=trip_id,
                    incident_type=IncidentType.BAGGAGE_MISMATCH,
                    reported_symptom="Manual pipeline run",
                    received_at=datetime.now(tz=UTC),
                )
            record = record.model_copy(update={
                "status": StageStatus.COMPLETED,
                "completed_at": datetime.now(tz=UTC),
            })
            return {
                "incident": inc,
                "stages": [*state.stages, record],
                "current_stage": "intake",
            }
        except Exception as e:
            record = record.model_copy(update={
                "status": StageStatus.FAILED,
                "error": str(e),
                "completed_at": datetime.now(tz=UTC),
            })
            return {"stages": [*state.stages, record], "current_stage": "intake"}

    def evidence_node(state: RunState) -> dict[str, Any]:
        record = StageRecord(
            stage="evidence",
            status=StageStatus.RUNNING,
            started_at=datetime.now(tz=UTC),
        )
        try:
            from oncall_rca.stages.evidence.engine import build_evidence_pack
            from oncall_rca.tools.log_api import fetch_trip_index

            if cache.has_trip_index(trip_id):
                raw_data = cache.read_trip_index(trip_id)
            elif offline:
                raise RuntimeError(f"Trip {trip_id} not in cache and offline=True")
            else:
                raw_data = fetch_trip_index(trip_id, settings)
                cache.write_trip_index(trip_id, raw_data)

            evidence_pack = build_evidence_pack(
                trip_id, raw_data, cache,
                settings=None if offline else settings,
            )
            record = record.model_copy(update={
                "status": StageStatus.COMPLETED,
                "completed_at": datetime.now(tz=UTC),
            })
            return {
                "evidence_pack": evidence_pack,
                "stages": [*state.stages, record],
                "current_stage": "evidence",
            }
        except Exception as e:
            record = record.model_copy(update={
                "status": StageStatus.FAILED,
                "error": str(e),
                "completed_at": datetime.now(tz=UTC),
            })
            return {"stages": [*state.stages, record], "current_stage": "evidence"}

    def code_context_node(state: RunState) -> dict[str, Any]:
        record = StageRecord(
            stage="code_context",
            status=StageStatus.RUNNING,
            started_at=datetime.now(tz=UTC),
        )
        try:
            evidence_pack = state.evidence_pack
            if evidence_pack is None or evidence_pack.divergence_point is None:
                ctx = CodeContext()
            else:
                from oncall_rca.stages.code_context.builder import build_code_context
                from oncall_rca.stages.repo_sync.sync import sync_repos

                repo_states = sync_repos(settings)
                if not repo_states:
                    ctx = CodeContext()
                else:
                    dp = evidence_pack.divergence_point
                    ctx = build_code_context(
                        step_api=dp.step.value,
                        step_api_type="",
                        repo_states=repo_states,
                        incident_date="",
                    )
            record = record.model_copy(update={
                "status": StageStatus.COMPLETED,
                "completed_at": datetime.now(tz=UTC),
            })
            return {
                "code_context": ctx,
                "stages": [*state.stages, record],
                "current_stage": "code_context",
            }
        except Exception:
            record = record.model_copy(update={
                "status": StageStatus.COMPLETED,
                "completed_at": datetime.now(tz=UTC),
            })
            return {
                "code_context": CodeContext(),
                "stages": [*state.stages, record],
                "current_stage": "code_context",
            }

    def investigator_node(state: RunState) -> dict[str, Any]:
        if state.evidence_pack is None:
            record = StageRecord(
                stage="investigator",
                status=StageStatus.SKIPPED,
                completed_at=datetime.now(tz=UTC),
            )
            return {"stages": [*state.stages, record], "current_stage": "investigator"}

        hypotheses: dict[int, Hypothesis] = {}
        for journey in state.evidence_pack.trip_index.journeys:
            ji = journey.journey_index
            try:
                h = investigate_journey(
                    state.evidence_pack,
                    ji,
                    state.code_context or CodeContext(),
                    llm,
                )
                hypotheses[ji] = h
            except Exception:
                pass

        first_h = next(iter(hypotheses.values()), None)
        record = StageRecord(
            stage="investigator",
            status=StageStatus.COMPLETED,
            completed_at=datetime.now(tz=UTC),
        )
        return {
            "hypotheses": hypotheses,
            "hypothesis": first_h,
            "stages": [*state.stages, record],
            "current_stage": "investigator",
        }

    def critic_node(state: RunState) -> dict[str, Any]:
        if not state.hypotheses or state.evidence_pack is None:
            record = StageRecord(
                stage="critic",
                status=StageStatus.SKIPPED,
                completed_at=datetime.now(tz=UTC),
            )
            return {"stages": [*state.stages, record], "current_stage": "critic"}

        critic_reports: dict[int, CriticReport] = {}
        for ji, h in state.hypotheses.items():
            try:
                cr = critique(h, state.evidence_pack, llm)
                critic_reports[ji] = cr
            except Exception:
                pass

        first_cr = next(iter(critic_reports.values()), None)
        record = StageRecord(
            stage="critic",
            status=StageStatus.COMPLETED,
            completed_at=datetime.now(tz=UTC),
        )
        return {
            "critic_reports": critic_reports,
            "critic_report": first_cr,
            "stages": [*state.stages, record],
            "current_stage": "critic",
        }

    def routing_node(state: RunState) -> dict[str, Any]:
        routing_decision = route_by_confidence(
            state.critic_reports,
            settings.routing,
        )
        return {"routing_decision": routing_decision}

    def rca_writer_node(state: RunState) -> dict[str, Any]:
        if not all([state.incident, state.evidence_pack, state.hypotheses, state.critic_reports]):
            record = StageRecord(
                stage="rca_writer",
                status=StageStatus.SKIPPED,
                completed_at=datetime.now(tz=UTC),
            )
            return {"stages": [*state.stages, record], "current_stage": "rca_writer"}

        rca_doc = build_rca_doc(
            state.incident,  # type: ignore[arg-type]
            state.evidence_pack,  # type: ignore[arg-type]
            state.code_context or CodeContext(),
            state.hypotheses,
            state.critic_reports,
        )
        save_rca(rca_doc, out_dir)

        record = StageRecord(
            stage="rca_writer",
            status=StageStatus.COMPLETED,
            completed_at=datetime.now(tz=UTC),
        )
        return {
            "rca_doc": rca_doc,
            "stages": [*state.stages, record],
            "current_stage": "rca_writer",
        }

    # --- Build graph ---

    graph = StateGraph(RunState)

    graph.add_node("intake", intake_node)
    graph.add_node("evidence", evidence_node)
    graph.add_node("code_context", code_context_node)
    graph.add_node("investigator", investigator_node)
    graph.add_node("critic", critic_node)
    graph.add_node("routing", routing_node)
    graph.add_node("rca_writer", rca_writer_node)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "evidence")
    graph.add_edge("evidence", "code_context")
    graph.add_edge("code_context", "investigator")
    graph.add_edge("investigator", "critic")
    graph.add_edge("critic", "routing")
    graph.add_edge("routing", "rca_writer")
    graph.add_edge("rca_writer", END)

    return graph.compile(checkpointer=MemorySaver())
