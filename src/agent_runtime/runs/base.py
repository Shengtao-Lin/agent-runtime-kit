"""Protocol for durable run state and invocation idempotency."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from agent_runtime.models import AgentDescriptor, RuntimeResponse
from agent_runtime.runs.models import RunClaim, RunRecord, RunStatus


class RunStore(Protocol):
    """Async durable run repository contract."""

    async def claim(
        self,
        *,
        run_id: UUID,
        request_id: UUID,
        agent: AgentDescriptor,
        thread_id: UUID,
        idempotency_key: str | None,
        request_hash: str,
    ) -> RunClaim: ...

    async def transition(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        response: RuntimeResponse | None = None,
        error_code: str | None = None,
    ) -> RunRecord: ...

    async def get(self, run_id: UUID) -> RunRecord | None: ...

    async def get_by_idempotency(
        self, *, agent_id: str, idempotency_key: str
    ) -> RunRecord | None: ...
