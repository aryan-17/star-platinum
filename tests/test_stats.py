"""Tests for run stats computation."""

from oncall_rca.observability.stats import compute_stats, format_stats
from oncall_rca.observability.trace import TraceRecord, TraceStore


class TestRunStats:
    def test_compute_from_traces(self) -> None:
        store = TraceStore()
        store.start_run("test-run")
        store.record(TraceRecord(
            stage="investigator", operation="llm_generate",
            input_tokens=1000, output_tokens=500, latency_ms=2000, success=True,
        ))
        store.record(TraceRecord(
            stage="critic", operation="llm_generate",
            input_tokens=800, output_tokens=300, latency_ms=1500, success=True,
        ))
        store.record(TraceRecord(
            stage="investigator", operation="llm_generate",
            input_tokens=200, output_tokens=100, latency_ms=500, success=False,
            error="timeout",
        ))

        stats = compute_stats(store)
        assert stats.run_id == "test-run"
        assert stats.total_calls == 3
        assert stats.total_input_tokens == 2000
        assert stats.total_output_tokens == 900
        assert stats.total_tokens == 2900
        assert stats.total_latency_ms == 4000
        assert stats.total_failures == 1
        assert stats.total_cost_usd > 0

    def test_per_stage_breakdown(self) -> None:
        store = TraceStore()
        store.start_run("test")
        store.record(TraceRecord(stage="intake", input_tokens=100, output_tokens=50, latency_ms=200))
        store.record(TraceRecord(stage="investigator", input_tokens=500, output_tokens=200, latency_ms=3000))

        stats = compute_stats(store)
        assert len(stats.stages) == 2
        inv = next(s for s in stats.stages if s.stage == "investigator")
        assert inv.input_tokens == 500
        assert inv.latency_ms == 3000

    def test_format_stats(self) -> None:
        store = TraceStore()
        store.start_run("fmt-test")
        store.record(TraceRecord(stage="investigator", input_tokens=1000, output_tokens=500, latency_ms=2000))

        stats = compute_stats(store)
        output = format_stats(stats)
        assert "fmt-test" in output
        assert "investigator" in output
        assert "TOTAL" in output
        assert "$" in output

    def test_empty_store(self) -> None:
        store = TraceStore()
        store.start_run("empty")
        stats = compute_stats(store)
        assert stats.total_calls == 0
        assert stats.total_cost_usd == 0.0
