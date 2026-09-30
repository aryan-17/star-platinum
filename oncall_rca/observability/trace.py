"""Observability: trace records for every LLM and tool call."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("oncall_rca.observability")


@dataclass
class TraceRecord:
    """A single trace record for an LLM or tool call."""

    trace_id: str = ""
    stage: str = ""
    operation: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    success: bool = True
    error: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, str] = field(default_factory=dict)


class TraceStore:
    """In-memory store for trace records within a run."""

    def __init__(self) -> None:
        self._records: list[TraceRecord] = []
        self._run_id: str = ""

    def start_run(self, run_id: str | None = None) -> str:
        """Start a new run, returning the run ID."""
        self._run_id = run_id or str(uuid.uuid4())[:12]
        self._records = []
        logger.info("Run started: %s", self._run_id)
        return self._run_id

    @property
    def run_id(self) -> str:
        return self._run_id

    def record(self, trace: TraceRecord) -> None:
        """Record a trace entry."""
        self._records.append(trace)
        logger.info(
            "[%s] %s.%s — %s — %dms — tokens: %d/%d",
            self._run_id,
            trace.stage,
            trace.operation,
            "OK" if trace.success else f"FAIL: {trace.error}",
            trace.latency_ms,
            trace.input_tokens,
            trace.output_tokens,
        )

    @property
    def records(self) -> list[TraceRecord]:
        return list(self._records)

    @property
    def total_tokens(self) -> int:
        return sum(r.input_tokens + r.output_tokens for r in self._records)

    @property
    def total_latency_ms(self) -> int:
        return sum(r.latency_ms for r in self._records)

    def summary(self) -> dict[str, int | str]:
        """Return a summary of the run's traces."""
        return {
            "run_id": self._run_id,
            "total_calls": len(self._records),
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "failures": sum(1 for r in self._records if not r.success),
        }


# Module-level singleton for the current run
trace_store = TraceStore()
