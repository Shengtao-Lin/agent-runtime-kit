"""OpenTelemetry configuration and privacy-aware tracing helpers."""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from agent_runtime.errors import RuntimeKitError
from agent_runtime.models import Message
from agent_runtime.telemetry.attributes import RUNTIME_VERSION

try:
    _package_version = version("agent-runtime-kit")
except PackageNotFoundError:
    _package_version = "0.0.0+uninstalled"


class RuntimeTelemetry:
    """Small tracing facade that keeps content capture an explicit choice."""

    def __init__(self, tracer: Tracer | None = None, *, capture_content: bool = False) -> None:
        self.tracer = tracer or trace.get_tracer("agent-runtime-kit")
        self.capture_content = capture_content

    @contextmanager
    def span(
        self, name: str, attributes: Mapping[str, object] | None = None
    ) -> Generator[Span, None, None]:
        """Start a span after filtering unsupported or absent attributes."""
        safe_attributes = self._safe_attributes(attributes or {})
        safe_attributes[RUNTIME_VERSION] = _package_version
        with self.tracer.start_as_current_span(
            name, attributes=safe_attributes, record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                yield span
            except BaseException as exc:
                span.set_status(Status(StatusCode.ERROR))
                span.add_event(
                    "agent.operation.failed",
                    {
                        "error.type": type(exc).__name__,
                        "error.code": exc.code
                        if isinstance(exc, RuntimeKitError)
                        else "operation_failed",
                    },
                )
                raise

    def annotate(self, attributes: Mapping[str, object]) -> None:
        """Attach trusted scalar identifiers to the current runtime span."""
        trace.get_current_span().set_attributes(self._safe_attributes(attributes))

    @staticmethod
    def _safe_attributes(attributes: Mapping[str, object]) -> dict[str, Any]:
        safe_attributes: dict[str, Any] = {}
        for key, value in attributes.items():
            if value is not None and isinstance(value, (str, bool, int, float)):
                safe_attributes[key] = value
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                items: list[object] = list(cast(Sequence[object], value))
                if all(isinstance(item, (str, bool, int, float)) for item in items):
                    safe_attributes[key] = items
        return safe_attributes

    def content_attributes(self, messages: list[Message]) -> dict[str, object]:
        """Return serialized message content only when explicitly enabled."""
        if not self.capture_content:
            return {}
        return {
            "agent.message.content": [
                message.model_dump_json(exclude={"id"}) for message in messages
            ]
        }

    @staticmethod
    def current_trace_id() -> str | None:
        """Return the active W3C trace identifier when a recording span exists."""
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return None
        return f"{context.trace_id:032x}"


class TelemetryHandle:
    """Own the SDK provider so applications can flush it during shutdown."""

    def __init__(self, telemetry: RuntimeTelemetry, provider: object | None = None) -> None:
        self.telemetry = telemetry
        self._provider = provider

    def shutdown(self) -> None:
        """Flush and stop the configured provider, if one exists."""
        shutdown = getattr(self._provider, "shutdown", None)
        if callable(shutdown):
            shutdown()


def configure_telemetry(
    *,
    service_name: str,
    endpoint: str | None = None,
    capture_content: bool = False,
) -> TelemetryHandle:
    """Configure an SDK tracer when available, falling back safely to no-op tracing."""
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return TelemetryHandle(RuntimeTelemetry(capture_content=capture_content))

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=2))
            )
        except ImportError:
            pass
    tracer = provider.get_tracer("agent-runtime-kit")
    return TelemetryHandle(
        RuntimeTelemetry(tracer, capture_content=capture_content),
        provider,
    )
