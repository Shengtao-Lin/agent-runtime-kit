"""Safe span errors and task-isolated JSON log correlation."""

import asyncio
import json
import logging
import sys

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from agent_runtime import __version__
from agent_runtime.service.logging import CorrelationFilter, JsonFormatter
from agent_runtime.telemetry import RuntimeTelemetry
from agent_runtime.telemetry.correlation import (
    bind_correlation,
    correlation_fields,
    set_correlation,
)


def test_span_attributes_include_version_and_errors_do_not_export_raw_text() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = RuntimeTelemetry(provider.get_tracer("test"))
    try:
        with pytest.raises(RuntimeError):
            with telemetry.span("agent.runtime.run"):
                telemetry.annotate({"agent.version": "one", "agent.framework": "native"})
                raise RuntimeError("private-exception-sentinel")
        span = exporter.get_finished_spans()[0]
        assert span.attributes is not None
        assert span.attributes["agent.runtime.version"] == __version__
        assert span.attributes["agent.version"] == "one"
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description is None
        assert "private-exception-sentinel" not in str(span.events)
        assert span.events[0].name == "agent.operation.failed"
    finally:
        provider.shutdown()


@pytest.mark.asyncio
async def test_concurrent_contexts_capture_identifiers_without_cross_request_leaks() -> None:
    async def capture(identity: str) -> dict[str, object]:
        with bind_correlation(request_id=identity, agent_id="agent", metadata="private"):
            set_correlation(run_id=f"run-{identity}", thread_id=f"thread-{identity}")
            await asyncio.sleep(0)
            record = logging.LogRecord("test", logging.INFO, "", 0, "safe_event", (), None)
            assert CorrelationFilter().filter(record)
        return json.loads(JsonFormatter().format(record))

    a, b = await asyncio.gather(capture("a"), capture("b"))
    assert a["request_id"] == "a" and a["run_id"] == "run-a"
    assert b["request_id"] == "b" and b["thread_id"] == "thread-b"
    assert "metadata" not in a and "private" not in str(a)
    assert correlation_fields() == {}


def test_nested_failure_restores_parent_and_exception_logging_is_categorical() -> None:
    with bind_correlation(request_id="parent"):
        with pytest.raises(ValueError):
            with bind_correlation(request_id="child"):
                raise ValueError("private-sentinel")
        assert correlation_fields()["request_id"] == "parent"
    assert correlation_fields() == {}
    try:
        raise ValueError("private-sentinel")
    except ValueError:
        record = logging.LogRecord("test", logging.ERROR, "", 0, "safe_failure", (), sys.exc_info())
    output = JsonFormatter().format(record)
    assert "ValueError" in output and "private-sentinel" not in output
