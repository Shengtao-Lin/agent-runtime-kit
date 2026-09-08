"""HTTP service response models derived from canonical runtime contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from agent_runtime.models import AgentDescriptor, StrictModel


class ErrorDetail(StrictModel):
    """Safe public error information."""

    code: str
    message: str
    retryable: bool = False


class ErrorEnvelope(StrictModel):
    """Versioned error envelope returned by all HTTP failures."""

    request_id: UUID
    error: ErrorDetail


class HealthResponse(StrictModel):
    """Process liveness response."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(StrictModel):
    """Database and migration readiness response."""

    status: Literal["ready", "not_ready"]


class AgentListResponse(StrictModel):
    """Discoverable registered agents."""

    agents: list[AgentDescriptor]
