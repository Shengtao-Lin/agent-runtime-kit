from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_runtime.models import Message, RuntimeRequest, TextContent, ToolResultContent


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
