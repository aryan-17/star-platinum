"""Tests for observability: trace store and logging."""

import json
import logging

from oncall_rca.observability.logging import JSONFormatter, setup_logging
from oncall_rca.observability.trace import TraceRecord, TraceStore


class TestTraceStore:
    def test_start_run(self) -> None:
        store = TraceStore()
        run_id = store.start_run("test-run-001")
        assert run_id == "test-run-001"
        assert store.run_id == "test-run-001"

    def test_auto_run_id(self) -> None:
        store = TraceStore()
        run_id = store.start_run()
        assert len(run_id) == 12

    def test_record_and_summary(self) -> None:
        store = TraceStore()
        store.start_run("test-run")

        store.record(TraceRecord(
            trace_id="t1",
            stage="intake",
            operation="classify",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200,
            success=True,
        ))
        store.record(TraceRecord(
            trace_id="t2",
            stage="investigator",
            operation="llm_generate",
            input_tokens=500,
            output_tokens=200,
            latency_ms=3000,
            success=False,
            error="timeout",
        ))

        assert len(store.records) == 2
        assert store.total_tokens == 850
        assert store.total_latency_ms == 3200

        summary = store.summary()
        assert summary["total_calls"] == 2
        assert summary["failures"] == 1

    def test_fresh_run_clears_records(self) -> None:
        store = TraceStore()
        store.start_run("run-1")
        store.record(TraceRecord(trace_id="t1"))
        assert len(store.records) == 1

        store.start_run("run-2")
        assert len(store.records) == 0


class TestJSONFormatter:
    def test_format_log_record(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="oncall_rca.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "test message"
        assert "timestamp" in parsed
