"""Public models for durable invocation records and idempotent replay."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from agent_runtime.models import AgentDescriptor, RuntimeResponse, StrictModel

RunStatus = Literal["accepted", "running", "succeeded", "failed", "blocked"]


class RunRecord(StrictModel):
    """A durable invocation state and optional canonical response."""

    id: UUID
    request_id: UUID
    agent: AgentDescriptor
    thread_id: UUID
    idempotency_key: str | None = None
    request_hash: str | None = None
    status: RunStatus
    response: RuntimeResponse | None = None
    error_code: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class RunClaim(StrictModel):
    """Result of claiming an invocation idempotency key."""

    run: RunRecord
    created: bool
