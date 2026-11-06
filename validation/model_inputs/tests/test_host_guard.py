"""Protected actual-wheel dispatches, durable failures and exact byte boundaries."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from host_policy import (
    ModelInputBudget,
    ModelInputExceeded,
    ModelInputInvalid,
    budget_model,
    canonical_size,
)
from input_cases import CASES, growth_host

from agent_runtime import Message, TextContent
from agent_runtime.errors import IdempotentRunUnavailableError
from agent_runtime.models import RuntimeContext, ToolResultContent, Usage
from agent_runtime.models_clients.base import ModelCompleted, ModelResult, StreamingModelClient
from agent_runtime.tools.models import ToolDefinition


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("mode", ["invoke", "stream", "fallback"])
async def test_growth_rejected_before_delegate_with_durable_failure(case, mode):
    before_tasks = set(asyncio.all_tasks())
    async with growth_host(
        os.environ["DATABASE_URL"], case, protected=True, generate_only=mode == "fallback"
    ) as app:
        request = app.request()
        events = []
        with pytest.raises(ModelInputExceeded) as failure:
            if mode == "invoke":
                await app.runtime.invoke(app.namespace, request, idempotency_key="guard")
            else:
                async for event in app.runtime.stream(
                    app.namespace, request, idempotency_key="guard"
                ):
                    events.append(event)
        assert not failure.value.retryable
        assert failure.value.public_message == "Model input exceeds the host budget"
        assert set(failure.value.internal_context) == {"dimension", "bytes", "limit"}
        expected_calls = CASES[case]["rounds"]
        assert len(app.model.calls) == expected_calls
        assert all(c["message_bytes"] <= 65536 and c["tool_bytes"] <= 8192 for c in app.model.calls)
        assert len(app.effects) == expected_calls * CASES[case]["calls_per_round"]
        assert app.model.opened_streams == app.model.closed_streams
        if mode != "stream":
            assert app.model.opened_streams == 0
        assert not any(event.type == "completed" for event in events)
        if mode == "stream" and expected_calls:
            assert any(event.type == "text_delta" for event in events)
        record = await app.runs.get_by_idempotency(agent_id=app.namespace, idempotency_key="guard")
        assert record.status == "failed"
        assert record.error_code == "host_model_input_exceeded"
        assert record.response is None
        assert not await app.memory.get_messages(record.thread_id, namespace=app.namespace)
        work = (len(app.model.calls), len(app.effects))
        with pytest.raises(IdempotentRunUnavailableError):
            await app.runtime.invoke(app.namespace, request, idempotency_key="guard")
        assert work == (len(app.model.calls), len(app.effects))
    await asyncio.sleep(0)
    assert not (set(asyncio.all_tasks()) - before_tasks)
    assert app.cleanup == {"pool_checkouts": 0, "database_sessions": 0}
    report = {
        "case": case,
        "mode": mode,
        "admitted_calls": app.model.calls,
        "rejection": failure.value.internal_context,
        "retained_tool_effects": len(app.effects),
        "provisional_events": [event.type for event in events],
        "durable_error": record.error_code,
        "replay_rejected_without_work": True,
        "cleanup": app.cleanup,
    }
    (Path(os.environ["MODEL_INPUT_REPORTS"]) / f"protected-{mode}-{case}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


class Echo:
    def __init__(self):
        self.calls = 0
        self.received = None
        self.result = ModelResult(
            message=Message(role="assistant", content=[TextContent(text="ok")]),
            usage=Usage(input_tokens=12, output_tokens=3),
        )

    async def generate(self, messages, *, tools=(), context):
        self.calls += 1
        self.received = (messages, tools, context)
        return self.result


class StreamingEcho(Echo):
    async def stream(self, messages, *, tools=(), context):
        yield ModelCompleted(result=await self.generate(messages, tools=tools, context=context))


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("dimension", ["messages", "tools"])
@pytest.mark.parametrize("mode", ["generate", "stream"])
async def test_complete_canonical_boundaries(dimension, offset, mode):
    # UTF-8, escaping, metadata-like nested JSON and multiple current messages are all counted.
    messages = [
        Message(role="user", content=[TextContent(text='雪🙂\\"\n')]),
        Message(role="user", content=[TextContent(text="second")]),
        Message(
            role="tool", content=[ToolResultContent(tool_call_id="t", result={"k": [1, True]})]
        ),
    ]
    tools = [
        ToolDefinition(name="emit", description="雪", input_schema={"type": "object"}),
        ToolDefinition(name="other", description="escaped\n", input_schema={"type": "object"}),
    ]
    snapshots = [m.model_dump(mode="json") for m in messages]
    counts = {"messages": canonical_size(messages), "tools": canonical_size(tools)}
    limits = dict(counts)
    limits[dimension] += offset
    context = RuntimeContext(run_id=uuid4(), thread_id=uuid4())
    delegate = StreamingEcho() if mode == "stream" else Echo()
    wrapped = budget_model(delegate, ModelInputBudget(limits["messages"], limits["tools"]))
    assert isinstance(wrapped, StreamingModelClient) == (mode == "stream")

    async def execute():
        if mode == "stream":
            return [
                event async for event in wrapped.stream(messages, tools=tools, context=context)
            ][-1].result
        return await wrapped.generate(messages, tools=tools, context=context)

    if offset < 0:
        with pytest.raises(ModelInputExceeded) as caught:
            await execute()
        assert caught.value.internal_context == {
            "dimension": dimension,
            "bytes": counts[dimension],
            "limit": limits[dimension],
        }
        assert delegate.calls == 0
    else:
        assert await execute() is delegate.result
        assert delegate.calls == 1
        assert delegate.received == (messages, tools, context)
    assert snapshots == [m.model_dump(mode="json") for m in messages]


@pytest.mark.parametrize("value", [True, 1, 0, -1, 2.5])
def test_invalid_configuration(value):
    with pytest.raises(ValueError):
        ModelInputBudget(value, 8192)


@pytest.mark.parametrize("text", ["\ud800", "\udfff"])
async def test_invalid_utf8_fails_without_dispatch_or_content(text):
    delegate = Echo()
    wrapped = budget_model(delegate, ModelInputBudget(65536, 8192))
    part = TextContent(text="valid")
    # Trusted host objects remain mutable; corruption after construction must fail closed.
    part.text = text
    with pytest.raises(ModelInputInvalid) as caught:
        await wrapped.generate(
            [Message(role="user", content=[part])],
            context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        )
    assert str(caught.value) == "Model input cannot be measured"
    assert not caught.value.retryable
    assert delegate.calls == 0
