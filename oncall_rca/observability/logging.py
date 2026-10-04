"""Structured logging setup for the RCA agent."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Merge any extra fields
        for key in ("run_id", "stage", "trace_id"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry, default=str)


def setup_logging(*, level: int = logging.INFO, json_output: bool = True) -> None:
    """Configure root logger for the application.

    Args:
        level: Log level.
        json_output: If True, emit structured JSON. If False, plain text.
    """
    root = logging.getLogger("oncall_rca")
    root.setLevel(level)

    if root.handlers:
        return  # already configured

    handler = logging.StreamHandler(sys.stderr)
    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        ))
    root.addHandler(handler)
