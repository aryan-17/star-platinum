"""Run stats — cost, latency, and token breakdown from trace records.

Generates a summary report from TraceStore data.
Can be printed to terminal or rendered as simple HTML.
"""

from __future__ import annotations

from dataclasses import dataclass

from oncall_rca.observability.trace import TraceStore


# Groq pricing (per 1M tokens, approximate)
_PRICING: dict[str, dict[str, float]] = {
    "openai/gpt-oss-120b": {"input": 0.50, "output": 1.50},
    "qwen/qwen3.8-27b": {"input": 0.18, "output": 0.18},
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "default": {"input": 0.50, "output": 1.50},
}


@dataclass
class StageStats:
    """Stats for a single stage."""

    stage: str
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int
    cost_usd: float
    failures: int


@dataclass
class RunStats:
    """Aggregate stats for an entire run."""

    run_id: str
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_latency_ms: int
    total_cost_usd: float
    total_failures: int
    stages: list[StageStats]


def compute_stats(store: TraceStore, model: str = "openai/gpt-oss-120b") -> RunStats:
    """Compute cost and latency stats from a TraceStore."""
    pricing = _PRICING.get(model, _PRICING["default"])

    stage_map: dict[str, StageStats] = {}
    for record in store.records:
        stage = record.stage or "unknown"
        if stage not in stage_map:
            stage_map[stage] = StageStats(
                stage=stage, calls=0, input_tokens=0, output_tokens=0,
                total_tokens=0, latency_ms=0, cost_usd=0.0, failures=0,
            )
        s = stage_map[stage]
        s.calls += 1
        s.input_tokens += record.input_tokens
        s.output_tokens += record.output_tokens
        s.total_tokens += record.input_tokens + record.output_tokens
        s.latency_ms += record.latency_ms
        s.cost_usd += (
            record.input_tokens * pricing["input"] / 1_000_000
            + record.output_tokens * pricing["output"] / 1_000_000
        )
        if not record.success:
            s.failures += 1

    stages = sorted(stage_map.values(), key=lambda s: s.stage)

    return RunStats(
        run_id=store.run_id,
        total_calls=sum(s.calls for s in stages),
        total_input_tokens=sum(s.input_tokens for s in stages),
        total_output_tokens=sum(s.output_tokens for s in stages),
        total_tokens=sum(s.total_tokens for s in stages),
        total_latency_ms=sum(s.latency_ms for s in stages),
        total_cost_usd=sum(s.cost_usd for s in stages),
        total_failures=sum(s.failures for s in stages),
        stages=stages,
    )


def format_stats(stats: RunStats) -> str:
    """Format stats as a terminal-friendly table."""
    lines: list[str] = []
    lines.append(f"Run: {stats.run_id}")
    lines.append(f"{'Stage':<20} {'Calls':>5} {'In Tok':>8} {'Out Tok':>8} {'Latency':>8} {'Cost':>8} {'Fail':>4}")
    lines.append("-" * 72)

    for s in stats.stages:
        lines.append(
            f"{s.stage:<20} {s.calls:>5} {s.input_tokens:>8} {s.output_tokens:>8} "
            f"{s.latency_ms:>7}ms ${s.cost_usd:>6.4f} {s.failures:>4}"
        )

    lines.append("-" * 72)
    lines.append(
        f"{'TOTAL':<20} {stats.total_calls:>5} {stats.total_input_tokens:>8} "
        f"{stats.total_output_tokens:>8} {stats.total_latency_ms:>7}ms "
        f"${stats.total_cost_usd:>6.4f} {stats.total_failures:>4}"
    )
    return "\n".join(lines)
