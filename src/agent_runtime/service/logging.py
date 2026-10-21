"""Minimal structured JSON logging without message-content capture."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from agent_runtime.telemetry.correlation import correlation_fields


class CorrelationFilter(logging.Filter):
    """Capture invocation identifiers before formatting or crossing a logging queue."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in correlation_fields().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    """Serialize safe operational fields as one JSON object per line."""

    _safe_fields = (
        "request_id",
        "run_id",
        "thread_id",
        "trace_id",
        "agent_id",
        "error_code",
        "status_code",
        "duration_ms",
    )

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record without serializing arbitrary extras."""
        document: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name in self._safe_fields:
            value = getattr(record, field_name, None)
            if value is not None:
                document[field_name] = value
        if record.exc_info is not None:
            exception_type = record.exc_info[0]
            document["exception_type"] = exception_type.__name__ if exception_type else "unknown"
        return json.dumps(document, ensure_ascii=True, separators=(",", ":"))
