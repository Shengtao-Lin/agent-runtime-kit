"""A real provider adapter must not commit provisional text from a truncated stream."""

from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime, RuntimeRequest
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.errors import IdempotentRunUnavailableError, ProviderError
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import Message, TextContent
from agent_runtime.models_clients.openai_compatible import OpenAICompatibleModelClient
from agent_runtime.runs import PostgresRunStore

pytestmark = pytest.mark.integration


async def test_truncated_provider_output_is_failed_and_never_reexecuted() -> None:
    url = os.getenv("AGENT_RUNTIME_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    memory = PostgresMemoryStore(engine)
    runs = PostgresRunStore(engine)
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.invalid"
    ) as transport:
        client = OpenAICompatibleModelClient(model="test", api_key="synthetic", client=transport)
        registry = AgentRegistry()
        registry.register(
            NativeAgentInvoker(agent_id="provider-failure", version="1", model_client=client)
        )
        runtime = AgentRuntime(agents=registry, memory=memory, runs=runs, namespace=str(uuid4()))
        key = str(uuid4())
        request = RuntimeRequest(
            messages=[Message(role="user", content=[TextContent(text="Hello")])]
        )
        try:
            observed: list[str] = []
            with pytest.raises(ProviderError):
                async for event in runtime.stream("provider-failure", request, idempotency_key=key):
                    observed.append(event.type)
            assert "completed" not in observed
            record = await runs.get_by_idempotency(agent_id="provider-failure", idempotency_key=key)
            assert record is not None
            assert record.status == "failed"
            assert record.error_code == "provider_failure"
            assert record.response is None
            assert await memory.get_messages(record.thread_id) == ()
            with pytest.raises(IdempotentRunUnavailableError):
                await runtime.invoke("provider-failure", request, idempotency_key=key)
            assert calls == 1
        finally:
            await runtime.shutdown()
            await engine.dispose()
