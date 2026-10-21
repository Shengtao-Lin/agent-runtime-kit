"""Public framework message translation and real runnable/graph execution."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent_runtime.adapters.framework_messages import (
    framework_input,
    from_framework_message,
    to_framework_message,
)
from agent_runtime.adapters.mapping import messages_output
from agent_runtime.errors import MaxToolIterationsError
from agent_runtime.models import (
    InvocationInput,
    Message,
    RuntimeContext,
    TextContent,
    ToolCall,
    ToolCallContent,
    ToolResultContent,
)
from agent_runtime.telemetry import RuntimeTelemetry
from examples.support_agent.agent import FakeSupportModel, build_agent_registry, build_tool_registry


def request() -> InvocationInput:
    return InvocationInput(
        context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        messages=[Message(role="user", content=[TextContent(text="Where is DEMO-42?")])],
    )


@pytest.mark.parametrize("role", ["system", "user", "assistant"])
def test_text_message_roundtrip_preserves_ids_and_blocks(role: str) -> None:
    message = Message.model_validate(
        {
            "role": role,
            "content": [
                {"type": "text", "text": "one"},
                {"type": "text", "text": "two"},
            ],
        }
    )
    assert from_framework_message(to_framework_message(message)) == message


def test_tool_message_roundtrip_and_native_output_retains_links_usage_and_new_messages() -> None:
    original = request()
    call = ToolCall(id="call-1", name="lookup_order", arguments={"order_id": "DEMO-42"})
    assistant = Message(role="assistant", content=[ToolCallContent(tool_call=call)])
    result = Message(
        role="tool", content=[ToolResultContent(tool_call_id=call.id, result={"ok": True})]
    )
    assert from_framework_message(to_framework_message(assistant)) == assistant
    assert from_framework_message(to_framework_message(result)) == result
    native = [
        *cast(list[BaseMessage], framework_input(original)["messages"]),
        to_framework_message(assistant),
        ToolMessage(id=str(result.id), content='{"ok":true}', tool_call_id=call.id),
        AIMessage(
            content="done",
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        ),
    ]
    output = messages_output({"messages": native}, original)
    assert [m.role for m in output.messages] == ["assistant", "tool", "assistant"]
    assert output.tool_results[0].tool_call_id == call.id
    assert output.tool_results[0].tool_name == "lookup_order"
    assert output.tool_results[0].output == {"ok": True}
    assert output.usage is not None and output.usage.input_tokens == 3


def test_non_uuid_ids_are_stable_and_direct_native_messages_are_supported() -> None:
    message = AIMessage(id="provider-id", content="answer")
    first = messages_output(message, request())
    second = messages_output([message], request())
    assert first.messages == second.messages
    assert from_framework_message(SystemMessage(content="system")).role == "system"
    assert from_framework_message(HumanMessage(content="human")).role == "user"


@pytest.mark.parametrize(
    "message",
    [
        AIMessage(content=[{"type": "image_url", "image_url": {"url": "synthetic"}}]),
        AIMessageChunk(content="partial"),
        AIMessage(
            content="",
            invalid_tool_calls=[{"name": "bad", "args": "{", "id": "bad", "error": "parse"}],
        ),
    ],
)
def test_unsupported_content_fails_closed(message: AIMessage) -> None:
    with pytest.raises(ValueError):
        from_framework_message(message)


@pytest.mark.parametrize("agent", ["support-native", "support-langchain", "support-langgraph"])
@pytest.mark.asyncio
async def test_real_frameworks_execute_shared_model_and_tool_flow(agent: str) -> None:
    telemetry = RuntimeTelemetry()
    registry = build_agent_registry(
        model_client=FakeSupportModel(),
        tools=build_tool_registry(telemetry=telemetry),
        telemetry=telemetry,
    )
    output = await registry.get(agent).invoke(request())
    assert [message.role for message in output.messages] == ["assistant", "tool", "assistant"]
    assert output.tool_results[0].tool_name == "lookup_order"
    assert output.tool_results[0].status == "succeeded"
    assert output.tool_results[0].output["order_id"] == "DEMO-42"


@pytest.mark.parametrize("agent", ["support-native", "support-langchain", "support-langgraph"])
@pytest.mark.asyncio
async def test_tool_limits_retain_canonical_error_in_every_framework(agent: str) -> None:
    class RepeatingModel(FakeSupportModel):
        async def generate(self, *args: object, **kwargs: object):
            from agent_runtime.models_clients.base import ModelResult

            call = ToolCall(
                id=str(uuid4()), name="get_store_policy", arguments={"topic": "returns"}
            )
            return ModelResult(
                message=Message(role="assistant", content=[ToolCallContent(tool_call=call)]),
                tool_calls=[call],
            )

    telemetry = RuntimeTelemetry()
    registry = build_agent_registry(
        model_client=RepeatingModel(),
        tools=build_tool_registry(telemetry=telemetry),
        telemetry=telemetry,
        max_tool_iterations=1,
    )
    with pytest.raises(MaxToolIterationsError):
        await registry.get(agent).invoke(request())
