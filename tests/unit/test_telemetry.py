from uuid import uuid4

from agent_runtime.models import Message, TextContent
from agent_runtime.telemetry import RuntimeTelemetry


def test_content_capture_is_disabled_by_default() -> None:
    message = Message(role="user", content=[TextContent(text="private prompt")])
    telemetry = RuntimeTelemetry()
    assert telemetry.content_attributes([message]) == {}


def test_content_capture_requires_explicit_opt_in() -> None:
    message = Message(id=uuid4(), role="user", content=[TextContent(text="visible prompt")])
    telemetry = RuntimeTelemetry(capture_content=True)
    attributes = telemetry.content_attributes([message])
    assert "visible prompt" in str(attributes["agent.message.content"])
    assert str(message.id) not in str(attributes["agent.message.content"])
