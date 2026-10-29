"""Framework-neutral orchestration across agents, policy, and persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from math import isfinite
from typing import Literal, cast
from uuid import UUID, uuid4

from agent_runtime._streams import checked_stream, close_source
from agent_runtime.adapters.base import StreamingAgentInvoker
from agent_runtime.errors import (
    AgentInvocationError,
    IdempotencyConflictError,
    IdempotentRunUnavailableError,
    InvocationTimeoutError,
    RunInProgressError,
    RunNotFoundError,
    RuntimeClosingError,
    RuntimeKitError,
    RuntimeShutdownTimeoutError,
    ThreadNotFoundError,
)
from agent_runtime.hooks import GuardrailBlockedError, HookEvent, HookManager
from agent_runtime.memory.base import MemoryStore
from agent_runtime.models import (
    InvocationInput,
    Message,
    RuntimeContext,
    RuntimeRequest,
    RuntimeResponse,
    RuntimeStreamEvent,
    StreamCompleted,
    StreamStarted,
    StreamTextDelta,
    StreamToolResult,
)
from agent_runtime.registry import AgentRegistry
from agent_runtime.runs.base import RunStore
from agent_runtime.runs.models import RunRecord
from agent_runtime.telemetry import RuntimeTelemetry
from agent_runtime.telemetry.attributes import AGENT_ID, REQUEST_ID
from agent_runtime.telemetry.correlation import (
    bind_correlation,
    correlation_fields,
    set_correlation,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class _InvocationState:
    # Set before claim: cancellation can arrive after the database committed it.
    run_id: UUID | None = None
    owner_token: UUID = field(default_factory=uuid4)


class AgentRuntime:
    """Apply shared runtime policy around any registered agent invoker."""

    def __init__(
        self,
        *,
        agents: AgentRegistry,
        memory: MemoryStore,
        runs: RunStore,
        hooks: HookManager | None = None,
        namespace: str = "default",
        invocation_timeout_seconds: float = 60.0,
        cleanup_timeout_seconds: float = 5.0,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        if not isfinite(invocation_timeout_seconds) or invocation_timeout_seconds <= 0:
            raise ValueError("invocation_timeout_seconds must be finite and positive")
        if not isfinite(cleanup_timeout_seconds) or cleanup_timeout_seconds <= 0:
            raise ValueError("cleanup_timeout_seconds must be finite and positive")
        if not isfinite(invocation_timeout_seconds + cleanup_timeout_seconds + 5):
            raise ValueError("Combined invocation and cleanup budgets must be finite")
        required = ("complete", "fail_if_running", "recover")
        if not all(callable(getattr(runs, name, None)) for name in required):
            raise TypeError("RunStore must implement complete(), fail_if_running(), and recover()")
        self._agents = agents
        self._memory = memory
        self._runs = runs
        self._hooks = hooks or HookManager()
        self._namespace = namespace
        self._timeout = invocation_timeout_seconds
        self._cleanup_timeout = cleanup_timeout_seconds
        self._telemetry = telemetry or RuntimeTelemetry()
        self._closing = False
        self._lifespan_active = False
        self._active_invocations: set[asyncio.Task[object]] = set()
        self._cleanup_tasks: set[asyncio.Task[RunRecord | None]] = set()

    @asynccontextmanager
    async def lifespan(
        self, *, shutdown_timeout_seconds: float = 5.0
    ) -> AsyncGenerator[AgentRuntime, None]:
        """Manage one runtime lifetime, without owning injected stores/models/telemetry.

        Enter after constructing host resources; exit after draining ingress and before closing
        those resources. A failed cooperative shutdown raises RuntimeShutdownTimeoutError.
        The runtime is single-lifetime: nested/concurrent entry and entry after shutdown fail.
        """
        if not isfinite(shutdown_timeout_seconds) or shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be finite and positive")
        if self._lifespan_active:
            raise RuntimeError("Runtime lifespan is already active")
        if self._closing:
            raise RuntimeClosingError("Runtime is closing")
        self._lifespan_active = True
        try:
            yield self
        finally:
            try:
                if not await self.shutdown(timeout_seconds=shutdown_timeout_seconds):
                    raise RuntimeShutdownTimeoutError("Runtime shutdown exceeded its budget")
            finally:
                self._lifespan_active = False

    async def shutdown(self, *, timeout_seconds: float = 5.0) -> bool:
        """Reject new work, cancel active calls, and await bounded cleanup before closing stores.

        Hosts call this after their ingress drain, before disposing shared resources.
        Returns false if cooperative teardown exceeds the host's additional budget.
        """
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        current = asyncio.current_task()
        if current in self._active_invocations:
            raise RuntimeError("Cannot shut down from inside an active invocation")
        self._closing = True
        for task in tuple(self._active_invocations):
            if not task.cancelling():
                task.cancel()
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while pending := {
            task for task in [*self._active_invocations, *self._cleanup_tasks] if not task.done()
        }:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                LOGGER.warning(
                    "runtime_shutdown_incomplete", extra={"error_code": "shutdown_timeout"}
                )
                return False
            await asyncio.wait(pending, timeout=remaining)
        return True

    async def invoke(
        self,
        agent_id: str,
        request: RuntimeRequest,
        *,
        idempotency_key: str | None = None,
    ) -> RuntimeResponse:
        """Execute one request or replay its completed idempotent response."""
        attributes: dict[str, object] = {
            AGENT_ID: agent_id,
            REQUEST_ID: str(request.request_id),
            "agent.runtime.contract_version": request.contract_version,
        }
        with self._telemetry.span("agent.runtime.run", attributes):
            return await self._invoke(agent_id, request, idempotency_key=idempotency_key)

    async def stream(
        self,
        agent_id: str,
        request: RuntimeRequest,
        *,
        idempotency_key: str | None = None,
    ) -> AsyncGenerator[RuntimeStreamEvent, None]:
        """Stream canonical events and finish with the persisted runtime response."""
        queue: asyncio.Queue[RuntimeStreamEvent | Exception | object] = asyncio.Queue(maxsize=64)
        sentinel = object()

        async def emit(event: RuntimeStreamEvent) -> None:
            await queue.put(event)

        async def produce() -> None:
            attributes: dict[str, object] = {
                AGENT_ID: agent_id,
                REQUEST_ID: str(request.request_id),
                "agent.runtime.contract_version": request.contract_version,
            }
            try:
                with self._telemetry.span("agent.runtime.run", attributes):
                    response = await self._invoke(
                        agent_id,
                        request,
                        idempotency_key=idempotency_key,
                        emit=emit,
                    )
                await queue.put(StreamCompleted(response=response))
            except Exception as exc:
                await queue.put(exc)
            # Cancellation must not wait for space in an abandoned consumer's queue.
            await queue.put(sentinel)

        task = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                if isinstance(item, Exception):
                    raise item
                yield cast(RuntimeStreamEvent, item)
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def _invoke(
        self,
        agent_id: str,
        request: RuntimeRequest,
        *,
        idempotency_key: str | None,
        emit: Callable[[RuntimeStreamEvent], Awaitable[None]] | None = None,
    ) -> RuntimeResponse:
        """Execute the internal runtime flow within the active runtime span."""
        if self._closing:
            raise RuntimeClosingError("Runtime is shutting down")
        task = asyncio.current_task()
        assert task is not None
        owns_registration = task not in self._active_invocations
        self._active_invocations.add(task)
        try:
            return await self._invoke_tracked(
                agent_id, request, idempotency_key=idempotency_key, emit=emit
            )
        finally:
            if owns_registration:
                self._active_invocations.discard(task)

    async def _invoke_tracked(
        self,
        agent_id: str,
        request: RuntimeRequest,
        *,
        idempotency_key: str | None,
        emit: Callable[[RuntimeStreamEvent], Awaitable[None]] | None,
    ) -> RuntimeResponse:
        with bind_correlation(request_id=str(request.request_id), agent_id=agent_id):
            try:
                response = await self._invoke_with_deadline(
                    agent_id, request, idempotency_key=idempotency_key, emit=emit
                )
            except asyncio.CancelledError:
                LOGGER.info(
                    "agent_invocation_cancelled",
                    extra={
                        **correlation_fields(),
                        "error_code": "invocation_cancelled",
                    },
                )
                raise
            except Exception as exc:
                LOGGER.info(
                    "agent_invocation_failed",
                    extra={
                        **correlation_fields(),
                        "error_code": exc.code
                        if isinstance(exc, RuntimeKitError)
                        else "internal_error",
                    },
                )
                raise
            self._annotate_run(response.run_id, response.thread_id)
            LOGGER.info("agent_invocation_completed", extra=correlation_fields())
            return response

    async def _invoke_with_deadline(
        self,
        agent_id: str,
        request: RuntimeRequest,
        *,
        idempotency_key: str | None,
        emit: Callable[[RuntimeStreamEvent], Awaitable[None]] | None,
    ) -> RuntimeResponse:
        state = _InvocationState()
        budget = asyncio.timeout(self._timeout)
        try:
            async with budget:
                return await self._execute(agent_id, request, idempotency_key, emit, state)
        except asyncio.CancelledError:
            if state.run_id is not None:
                await self._record_failure(
                    state.run_id, owner_token=state.owner_token, error_code="invocation_cancelled"
                )
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError) and budget.expired():
                error: Exception = InvocationTimeoutError("Runtime invocation deadline exceeded")
            else:
                error = exc
            error_code = error.code if isinstance(error, RuntimeKitError) else "internal_error"
            status = "blocked" if isinstance(error, GuardrailBlockedError) else "failed"
            if state.run_id is not None:
                record = await self._record_failure(
                    state.run_id,
                    owner_token=state.owner_token,
                    error_code=error_code,
                    status=status,
                )
                if (
                    record is not None
                    and record.status == "succeeded"
                    and record.response is not None
                ):
                    return record.response
            if error is not exc:
                raise error from exc
            raise

    async def _execute(
        self,
        agent_id: str,
        request: RuntimeRequest,
        idempotency_key: str | None,
        emit: Callable[[RuntimeStreamEvent], Awaitable[None]] | None,
        state: _InvocationState,
    ) -> RuntimeResponse:
        """Run all asynchronous phases under a single monotonic timeout budget."""
        invoker = self._agents.get(agent_id)
        self._telemetry.annotate(
            {
                "agent.id": agent_id,
                "agent.version": invoker.descriptor.version,
                "agent.framework": invoker.descriptor.framework,
            }
        )
        request_hash = self.request_hash(request)
        if idempotency_key is not None:
            previous = await self._runs.get_by_idempotency(
                agent_id=agent_id, idempotency_key=idempotency_key
            )
            if previous is not None:
                await self._require_scoped_thread(previous.thread_id)
                self._annotate_run(previous.id, previous.thread_id)
                if previous.request_hash == request_hash and previous.status in {
                    "accepted",
                    "running",
                }:
                    previous = await self._runs.recover(previous.id)
                replay = self._replay(previous, request_hash)
                if emit is not None:
                    await emit(self._started(replay))
                return replay

        if request.thread_id is None:
            thread = await self._memory.create_thread(
                namespace=self._namespace,
                user_key=request.user_id,
            )
        else:
            thread = await self._memory.get_thread(request.thread_id, namespace=self._namespace)
            if thread is None:
                raise ThreadNotFoundError(f"Thread '{request.thread_id}' does not exist")

        run_id = uuid4()
        context = RuntimeContext(
            run_id=run_id,
            thread_id=thread.id,
            user_id=request.user_id,
        )
        state.run_id = run_id
        self._annotate_run(run_id, thread.id)
        claim = await self._runs.claim(
            run_id=run_id,
            request_id=request.request_id,
            agent=invoker.descriptor,
            thread_id=thread.id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            owner_token=state.owner_token,
            lease_seconds=self._timeout + self._cleanup_timeout + 5,
        )
        if not claim.created:
            state.run_id = None
            await self._require_scoped_thread(claim.run.thread_id)
            self._annotate_run(claim.run.id, claim.run.thread_id)
            if claim.run.status in {"accepted", "running"}:
                claim = claim.model_copy(update={"run": await self._runs.recover(claim.run.id)})
            replay = self._replay(claim.run, request_hash)
            if emit is not None:
                await emit(self._started(replay))
            return replay

        await self._runs.transition(run_id, "running", owner_token=state.owner_token)
        if emit is not None:
            await emit(
                StreamStarted(
                    run_id=run_id,
                    request_id=request.request_id,
                    thread_id=thread.id,
                    agent=invoker.descriptor,
                )
            )
        history = await self._memory.get_messages(thread.id, namespace=self._namespace)
        input_messages = [item.message for item in history] + list(request.messages)
        await self._hooks.run(
            HookEvent(phase="before_model", context=context, messages=input_messages)
        )
        invocation = InvocationInput(
            context=context, messages=input_messages, metadata=request.metadata
        )
        if emit is not None and isinstance(invoker, StreamingAgentInvoker):
            output = None
            stream = checked_stream(
                invoker.stream(invocation),
                is_completed=lambda event: event.type == "completed",
                failure=AgentInvocationError,
            )
            try:
                async for event in stream:
                    if event.type == "text_delta":
                        await emit(StreamTextDelta(delta=event.delta))
                    elif event.type == "tool_result":
                        await emit(StreamToolResult(result=event.result))
                    else:
                        output = event.output
            finally:
                await close_source(stream)
            if output is None:
                raise AgentInvocationError("Agent stream ended without a completed output")
        else:
            output = await invoker.invoke(invocation)
        await self._hooks.run(
            HookEvent(phase="after_model", context=context, messages=output.messages)
        )
        assistant = self._final_assistant(output.messages)
        response = RuntimeResponse(
            run_id=run_id,
            request_id=request.request_id,
            thread_id=thread.id,
            agent=invoker.descriptor,
            message=assistant,
            tool_results=output.tool_results,
            usage=output.usage,
            trace_id=self._telemetry.current_trace_id(),
            metadata=output.metadata,
        )
        await self._runs.complete(
            run_id,
            owner_token=state.owner_token,
            messages=[*request.messages, *output.messages],
            response=response,
        )
        return response

    def _annotate_run(self, run_id: UUID, thread_id: UUID) -> None:
        set_correlation(run_id=str(run_id), thread_id=str(thread_id))
        self._telemetry.annotate({"agent.run.id": str(run_id), "agent.thread.id": str(thread_id)})

    async def _require_scoped_thread(self, thread_id: UUID) -> None:
        if await self._memory.get_thread(thread_id, namespace=self._namespace) is None:
            raise ThreadNotFoundError(f"Thread '{thread_id}' does not exist")

    async def _record_failure(
        self,
        run_id: UUID,
        *,
        owner_token: UUID,
        error_code: str,
        status: Literal["failed", "blocked"] = "failed",
    ) -> RunRecord | None:
        """Reconcile uncertain commits without masking the original error or a durable success."""
        task = asyncio.create_task(
            self._settle_failure(
                run_id,
                owner_token=owner_token,
                error_code=error_code,
                status=status,
            )
        )
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)
        return await asyncio.shield(task)

    async def _settle_failure(
        self,
        run_id: UUID,
        *,
        owner_token: UUID,
        error_code: str,
        status: Literal["failed", "blocked"],
    ) -> RunRecord | None:
        try:
            async with asyncio.timeout(self._cleanup_timeout):
                return await self._runs.fail_if_running(
                    run_id, owner_token=owner_token, error_code=error_code, status=status
                )
        except RunNotFoundError:
            # Claim may have been canceled before its transaction committed.
            return None
        except Exception:
            LOGGER.warning(
                "run_cleanup_unavailable",
                extra={**correlation_fields(), "run_id": str(run_id), "error_code": error_code},
            )
            return None

    @staticmethod
    def request_hash(request: RuntimeRequest) -> str:
        """Hash semantically relevant request fields for idempotency comparison."""
        payload = request.model_dump(mode="json", exclude={"request_id"})
        for message in payload["messages"]:
            message.pop("id", None)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _replay(run: RunRecord, request_hash: str) -> RuntimeResponse:
        if run.request_hash != request_hash:
            raise IdempotencyConflictError(
                "Idempotency key was already used with a different request"
            )
        if run.status in {"accepted", "running"}:
            raise RunInProgressError("The idempotent request is still running")
        if run.status == "succeeded" and run.response is not None:
            return run.response
        raise IdempotentRunUnavailableError(
            f"The prior idempotent run completed with status '{run.status}'"
        )

    @staticmethod
    def _final_assistant(messages: list[Message]) -> Message:
        for message in reversed(messages):
            if message.role == "assistant":
                return message
        raise ValueError("Agent output must contain an assistant message")

    @staticmethod
    def _started(response: RuntimeResponse) -> StreamStarted:
        return StreamStarted(
            run_id=response.run_id,
            request_id=response.request_id,
            thread_id=response.thread_id,
            agent=response.agent,
        )
