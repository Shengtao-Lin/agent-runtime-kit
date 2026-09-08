"""Framework-neutral orchestration across agents, policy, and persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import suppress
from typing import cast
from uuid import uuid4

from agent_runtime.adapters.base import StreamingAgentInvoker
from agent_runtime.errors import (
    IdempotencyConflictError,
    IdempotentRunUnavailableError,
    InvocationTimeoutError,
    RunInProgressError,
    RuntimeKitError,
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
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        if invocation_timeout_seconds <= 0:
            raise ValueError("invocation_timeout_seconds must be positive")
        self._agents = agents
        self._memory = memory
        self._runs = runs
        self._hooks = hooks or HookManager()
        self._namespace = namespace
        self._timeout = invocation_timeout_seconds
        self._telemetry = telemetry or RuntimeTelemetry()

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
            finally:
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
        invoker = self._agents.get(agent_id)
        request_hash = self.request_hash(request)
        if idempotency_key is not None:
            previous = await self._runs.get_by_idempotency(
                agent_id=agent_id, idempotency_key=idempotency_key
            )
            if previous is not None:
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
            thread = await self._memory.get_thread(request.thread_id)
            if thread is None:
                raise ThreadNotFoundError(f"Thread '{request.thread_id}' does not exist")

        run_id = uuid4()
        context = RuntimeContext(
            run_id=run_id,
            thread_id=thread.id,
            user_id=request.user_id,
        )
        claim = await self._runs.claim(
            run_id=run_id,
            request_id=request.request_id,
            agent=invoker.descriptor,
            thread_id=thread.id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if not claim.created:
            replay = self._replay(claim.run, request_hash)
            if emit is not None:
                await emit(self._started(replay))
            return replay

        await self._runs.transition(run_id, "running")
        if emit is not None:
            await emit(
                StreamStarted(
                    run_id=run_id,
                    request_id=request.request_id,
                    thread_id=thread.id,
                    agent=invoker.descriptor,
                )
            )
        try:
            history = await self._memory.get_messages(thread.id)
            input_messages = [item.message for item in history] + list(request.messages)
            await self._hooks.run(
                HookEvent(
                    phase="before_model",
                    context=context,
                    messages=input_messages,
                )
            )
            try:
                async with asyncio.timeout(self._timeout):
                    invocation = InvocationInput(
                        context=context,
                        messages=input_messages,
                        metadata=request.metadata,
                    )
                    if emit is not None and isinstance(invoker, StreamingAgentInvoker):
                        output = None
                        async for event in invoker.stream(invocation):
                            if event.type == "text_delta":
                                await emit(StreamTextDelta(delta=event.delta))
                            elif event.type == "tool_result":
                                await emit(StreamToolResult(result=event.result))
                            else:
                                output = event.output
                        if output is None:
                            raise ValueError("Agent stream ended without a completed output")
                    else:
                        output = await invoker.invoke(invocation)
            except TimeoutError as exc:
                raise InvocationTimeoutError("Agent invocation timed out") from exc
            await self._hooks.run(
                HookEvent(
                    phase="after_model",
                    context=context,
                    messages=output.messages,
                )
            )
            assistant = self._final_assistant(output.messages)
            await self._memory.append_messages(
                thread.id,
                [*request.messages, *output.messages],
            )
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
            await self._runs.transition(run_id, "succeeded", response=response)
            return response
        except GuardrailBlockedError as exc:
            await self._runs.transition(run_id, "blocked", error_code=exc.code)
            raise
        except asyncio.CancelledError:
            await self._runs.transition(run_id, "failed", error_code="invocation_cancelled")
            raise
        except Exception as exc:
            error_code = exc.code if isinstance(exc, RuntimeKitError) else "internal_error"
            await self._runs.transition(run_id, "failed", error_code=error_code)
            raise

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
