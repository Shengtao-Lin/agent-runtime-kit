"""FastAPI application for the fictional support-agent example."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRuntime
from agent_runtime.config import RuntimeSettings
from agent_runtime.feedback import PostgresFeedbackStore
from agent_runtime.hooks import (
    GuardrailHook,
    HookManager,
    MessageLengthGuardrail,
    PhraseBlockGuardrail,
)
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models_clients.base import ModelClient
from agent_runtime.models_clients.openai_compatible import OpenAICompatibleModelClient
from agent_runtime.runs import PostgresRunStore
from agent_runtime.service import create_app
from agent_runtime.telemetry import configure_telemetry
from examples.support_agent.agent import FakeSupportModel, build_agent_registry, build_tool_registry


def build_application(settings: RuntimeSettings | None = None) -> FastAPI:
    """Construct the complete example application and owned resources."""
    settings = settings or RuntimeSettings()
    telemetry_handle = configure_telemetry(
        service_name=settings.otel_service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
        capture_content=settings.otel_capture_content,
    )
    telemetry = telemetry_handle.telemetry
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_pre_ping=True,
    )
    memory = PostgresMemoryStore(engine, telemetry)
    runs = PostgresRunStore(engine)
    feedback = PostgresFeedbackStore(engine, telemetry)
    model_client: ModelClient
    close_model = None
    if settings.fake_model:
        model_client = FakeSupportModel()
    else:
        if settings.model_api_key is None:
            raise ValueError("MODEL_API_KEY is required when FAKE_MODEL=false")
        model = OpenAICompatibleModelClient(
            model=settings.model_name,
            api_key=settings.model_api_key.get_secret_value(),
            base_url=settings.model_base_url or "https://api.openai.com/v1",
            timeout_seconds=settings.invocation_timeout_seconds,
        )
        model_client = model
        close_model = model.close

    hooks = HookManager(
        [
            GuardrailHook(
                PhraseBlockGuardrail(["demo-blocked-phrase"]),
                phases={"before_model"},
                telemetry=telemetry,
            ),
            GuardrailHook(
                MessageLengthGuardrail(32_000),
                phases={"before_model", "after_model"},
                telemetry=telemetry,
            ),
        ]
    )
    tools = build_tool_registry(
        telemetry=telemetry,
        timeout_seconds=settings.tool_timeout_seconds,
    )
    agents = build_agent_registry(
        model_client=model_client,
        tools=tools,
        telemetry=telemetry,
        max_tool_iterations=settings.max_tool_iterations,
    )
    runtime = AgentRuntime(
        agents=agents,
        memory=memory,
        runs=runs,
        hooks=hooks,
        namespace="fictional-support",
        invocation_timeout_seconds=settings.invocation_timeout_seconds,
        telemetry=telemetry,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        del application
        await memory.put(
            namespace="fictional-support-preferences",
            user_key="synthetic-demo-user",
            memory_key="language",
            value={"language": "en"},
            tags={"kind": "preference"},
        )
        try:
            yield
        finally:
            if close_model is not None:
                await close_model()
            await memory.close()
            telemetry_handle.shutdown()

    return create_app(
        runtime=runtime,
        agents=agents,
        feedback=feedback,
        readiness=memory,
        max_request_body_bytes=settings.max_request_body_bytes,
        lifespan=lifespan,
    )


app = build_application()
