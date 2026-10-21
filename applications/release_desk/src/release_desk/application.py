"""Compose an installed library with application-owned policy, routing and resources."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.feedback import PostgresFeedbackStore
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import RuntimeContext, ToolCall, ToolCallContent, ToolResultContent
from agent_runtime.models_clients.base import (
    ModelCompleted,
    ModelResult,
    ModelStreamEvent,
    ModelTextDelta,
)
from agent_runtime.runs import PostgresRunStore
from agent_runtime.tools import ToolDefinition, ToolRegistry
from release_desk.review import (
    GitHubReleaseSource,
    ReleaseSource,
    ReviewInput,
    ReviewReport,
    review_release,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RELEASE_DESK_", extra="ignore")
    database_url: SecretStr
    namespace: str = Field(default="release-desk", min_length=1, max_length=128)


class ReviewRouter:
    """Deterministic command routing; no model account, hidden network or prompt dependency."""

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult:
        del tools
        if messages[-1].role == "tool":
            part = next(
                part for part in messages[-1].content if isinstance(part, ToolResultContent)
            )
            report = ReviewReport.model_validate(part.result)
            text = f"{report.repository}@{report.tag}: " + (
                "metadata ready" if report.metadata_ready else "metadata incomplete"
            )
            text += (
                "\n"
                + "\n".join(
                    f"{'PASS' if check.passed else 'FAIL'} {check.name}" for check in report.checks
                )
                + "\n"
                + report.boundary
            )
            return ModelResult(message=Message(role="assistant", content=[TextContent(text=text)]))
        command = next(part.text for part in messages[-1].content if isinstance(part, TextContent))
        request = ReviewInput.model_validate_json(command)
        call = ToolCall(
            id=f"review-{context.run_id}", name="inspect_release", arguments=request.model_dump()
        )
        return ModelResult(
            message=Message(role="assistant", content=[ToolCallContent(tool_call=call)]),
            tool_calls=[call],
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        result = await self.generate(messages, tools=tools, context=context)
        for part in result.message.content:
            if isinstance(part, TextContent):
                for line in part.text.splitlines(keepends=True):
                    yield ModelTextDelta(delta=line)
        yield ModelCompleted(result=result)


@dataclass
class Application:
    agent_id: str
    runtime: AgentRuntime
    memory: PostgresMemoryStore
    feedback: PostgresFeedbackStore

    def request(self, review: ReviewInput, *, thread_id: UUID | None = None) -> RuntimeRequest:
        return RuntimeRequest(
            thread_id=thread_id,
            messages=[Message(role="user", content=[TextContent(text=review.model_dump_json())])],
        )


@asynccontextmanager
async def application(
    settings: Settings, *, source: ReleaseSource | None = None
) -> AsyncGenerator[Application, None]:
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=True,
    )
    memory = PostgresMemoryStore(engine)
    runtime = None
    try:
        if not await memory.check_ready() or not await memory.check_migrations("0002"):
            raise RuntimeError(
                "Database is unavailable or schema 0002 is missing; migrate explicitly"
            )
        release_source = source if source is not None else GitHubReleaseSource()

        async def inspect_release(
            repository: str, tag: str, distribution: str
        ) -> dict[str, object]:
            result = await review_release(
                release_source,
                ReviewInput(repository=repository, tag=tag, distribution=distribution),
            )
            return result.model_dump(mode="json")

        tools = ToolRegistry()
        tools.register(
            name="inspect_release",
            description="Inspect public Python release metadata",
            input_model=ReviewInput,
            handler=inspect_release,
            timeout_seconds=12,
        )
        agent_id = "release-desk-" + hashlib.sha256(settings.namespace.encode()).hexdigest()[:16]
        agents = AgentRegistry()
        agents.register(
            NativeAgentInvoker(
                agent_id=agent_id,
                version="0.1.0",
                model_client=ReviewRouter(),
                tools=tools,
                max_tool_iterations=1,
                capabilities={"release-review", "tools", "streaming"},
            )
        )
        runtime = AgentRuntime(
            agents=agents,
            memory=memory,
            runs=PostgresRunStore(engine),
            namespace=settings.namespace,
            invocation_timeout_seconds=30,
        )
        yield Application(agent_id, runtime, memory, PostgresFeedbackStore(engine))
    finally:
        try:
            if runtime is not None and not await runtime.shutdown(timeout_seconds=6):
                raise RuntimeError("Shutdown exceeded its budget; inspect durable run state")
        finally:
            await engine.dispose()
