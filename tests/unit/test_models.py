from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_runtime.models import (
    AgentDescriptor,
    Message,
    RuntimeRequest,
    TextContent,
    ToolCall,
    ToolCallContent,
    ToolResultContent,
)


def test_agent_capabilities_serialize_in_stable_order() -> None:
    descriptor = AgentDescriptor(
        agent_id="support",
        version="1.0.0",
        framework="native",
        capabilities={"tools", "multi_turn"},
    )

    assert descriptor.model_dump(mode="json")["capabilities"] == ["multi_turn", "tools"]


def test_runtime_request_generates_stable_identifiers() -> None:
    request = RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="Hello")])])

    assert request.contract_version == "v1"
    assert request.request_id


def test_request_rejects_unknown_contract_fields() -> None:
    with pytest.raises(ValidationError):
        RuntimeRequest.model_validate(
            {
                "messages": [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}],
                "unexpected": True,
            }
        )


def test_tool_message_requires_tool_result() -> None:
    with pytest.raises(ValidationError, match="tool_result"):
        Message(role="tool", content=[TextContent(text="not a result")])

    message = Message(
        id=uuid4(),
        role="tool",
        content=[ToolResultContent(tool_call_id="call-1", result={"ok": True})],
    )
    assert message.role == "tool"


def test_message_roles_reject_mismatched_tool_content() -> None:
    call = ToolCall(id="call-1", name="lookup", arguments={})
    with pytest.raises(ValidationError, match="assistant role"):
        Message(role="user", content=[ToolCallContent(tool_call=call)])
    with pytest.raises(ValidationError, match="tool role"):
        Message(
            role="assistant",
            content=[ToolResultContent(tool_call_id="call-1", result=True)],
        )


def test_metadata_is_bounded_and_json_compatible() -> None:
    message = Message(role="user", content=[TextContent(text="Hello")])
    with pytest.raises(ValidationError, match="64 keys"):
        RuntimeRequest(messages=[message], metadata={str(index): index for index in range(65)})
    with pytest.raises(ValidationError, match="16384 encoded bytes"):
        RuntimeRequest(messages=[message], metadata={"large": "x" * 16_384})
    with pytest.raises(ValidationError, match="JSON-compatible"):
        RuntimeRequest(messages=[message], metadata={"unsupported": object()})
