"""Durable run state and invocation idempotency."""

from typing import TYPE_CHECKING

from agent_runtime.runs.base import RunStore
from agent_runtime.runs.models import RunClaim, RunRecord, RunStatus

if TYPE_CHECKING:
    from agent_runtime.runs.postgres import PostgresRunStore

__all__ = ["PostgresRunStore", "RunClaim", "RunRecord", "RunStatus", "RunStore"]


def __getattr__(name: str) -> object:
    """Load the optional PostgreSQL implementation only when requested."""
    if name == "PostgresRunStore":
        from agent_runtime.runs.postgres import PostgresRunStore

        return PostgresRunStore
    raise AttributeError(name)
