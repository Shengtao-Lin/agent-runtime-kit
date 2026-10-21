"""Task-local operational identifiers, never copied from caller metadata."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry import trace

_FIELDS = frozenset({"request_id", "run_id", "thread_id", "agent_id"})
_context: ContextVar[dict[str, str] | None] = ContextVar("agent_runtime_correlation", default=None)


def set_correlation(**fields: str) -> None:
    """Bind only trusted identifiers to a copy of the current task's context."""
    values = dict(_context.get() or {})
    values.update({key: value for key, value in fields.items() if key in _FIELDS})
    _context.set(values)


def correlation_fields() -> dict[str, str]:
    """Capture safe identifiers at log emission time, including the active trace."""
    values = dict(_context.get() or {})
    span = trace.get_current_span().get_span_context()
    if span.is_valid:
        values["trace_id"] = f"{span.trace_id:032x}"
    return values


@contextmanager
def bind_correlation(**fields: str) -> Generator[None, None, None]:
    """Scope identifiers to one invocation, restoring the parent even after cancellation."""
    token = _context.set({})
    set_correlation(**fields)
    try:
        yield
    finally:
        _context.reset(token)
