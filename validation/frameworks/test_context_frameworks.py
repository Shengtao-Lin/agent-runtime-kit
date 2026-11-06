"""Real frameworks receive selected canonical entry messages, with durable full history."""

import asyncio
import json

import pytest
from consumer_app import request
from framework_app import application

from agent_runtime import Message, TextContent
from agent_runtime.context import ContextWindow
from agent_runtime.errors import IdempotentRunUnavailableError
from agent_runtime.hooks import HookManager

pytestmark = pytest.mark.asyncio


class Observer:
    name = "selected-entry"

    def __init__(self):
        self.inputs = []

    async def handle(self, event):
        if event.phase == "before_model":
            self.inputs.append(event)


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_framework_context_entry_and_replay(database_url, identity, framework, streaming):
    policy = Message(role="system", content=[TextContent(text="retain policy")])
    current = request("quote")
    expected = [policy, *current.messages]
    budget = len(
        json.dumps([m.model_dump(mode="json") for m in expected], separators=(",", ":")).encode()
    )
    observer = Observer()
    async with application(
        database_url,
        identity,
        framework,
        hooks=HookManager([observer]),
        context_window=ContextWindow(budget),
    ) as app:
        thread = await app.memory.create_thread(namespace=identity)
        history = [policy, Message(role="user", content=[TextContent(text="old" * 1000)])]
        await app.memory.append_messages(thread.id, history)
        current.thread_id = thread.id
        if streaming:
            events = [
                event
                async for event in app.runtime.stream(identity, current, idempotency_key="window")
            ]
            assert events[-1].type == "completed"
            response = events[-1].response
        else:
            response = await app.runtime.invoke(identity, current, idempotency_key="window")
        assert app.seen_history == [2]
        assert observer.inputs[0].messages == expected
        assert (
            observer.inputs[0].context.attributes["agent_runtime.context"][
                "omitted_history_messages"
            ]
            == 1
        )
        assert app.tool_calls == [3]
        stored = [item.message for item in await app.memory.get_messages(thread.id)]
        assert stored[:3] == [*history, *current.messages]
        assert len(stored) == 6  # Two historical messages plus a complete four-message turn.
        assert await app.runtime.invoke(identity, current, idempotency_key="window") == response
        assert app.seen_history == [2] and len(observer.inputs) == 1


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
async def test_framework_cancel_after_selection(database_url, identity, framework):
    async with application(
        database_url, identity, framework, context_window=ContextWindow(512)
    ) as app:
        thread = await app.memory.create_thread(namespace=identity)
        history = [Message(role="user", content=[TextContent(text="old" * 1000)])]
        await app.memory.append_messages(thread.id, history)
        current = request("wait", thread_id=thread.id)
        task = asyncio.create_task(app.runtime.invoke(identity, current, idempotency_key="cancel"))
        try:
            await asyncio.wait_for(app.entered.wait(), timeout=3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert app.cancelled.is_set() and app.seen_history == [1]
            assert [item.message for item in await app.memory.get_messages(thread.id)] == history
            with pytest.raises(IdempotentRunUnavailableError):
                await app.runtime.invoke(identity, current, idempotency_key="cancel")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
