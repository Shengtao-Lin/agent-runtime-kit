"""Default dictionary mappings shared by optional framework adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from agent_runtime.models import InvocationInput, InvocationOutput, Message


def messages_input(request: InvocationInput) -> dict[str, object]:
    """Map canonical input to a common message-oriented framework payload."""
    return {
        "messages": [message.model_dump(mode="json") for message in request.messages],
        "metadata": request.metadata,
        "context": request.context.model_dump(mode="json"),
    }


def messages_output(value: object, request: InvocationInput) -> InvocationOutput:
    """Map canonical-looking framework output dictionaries back to the contract."""
    del request
    if isinstance(value, InvocationOutput):
        return value
    if isinstance(value, Message):
        return InvocationOutput(messages=[value])
    if not isinstance(value, Mapping):
        raise TypeError("Adapter output must be a message, mapping, or InvocationOutput")

    output_mapping = cast(Mapping[object, object], value)
    raw_messages = output_mapping.get("messages")
    if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
        raise TypeError("Adapter output mapping must contain a message sequence")
    message_values = cast(Sequence[object], raw_messages)
    messages = [Message.model_validate(item) for item in message_values]
    metadata_value = output_mapping.get("metadata", {})
    if not isinstance(metadata_value, Mapping):
        raise TypeError("Adapter output metadata must be a mapping")
    metadata_mapping = cast(Mapping[object, object], metadata_value)
    metadata: dict[str, Any] = {str(key): item for key, item in metadata_mapping.items()}
    return InvocationOutput(messages=messages, metadata=metadata)
