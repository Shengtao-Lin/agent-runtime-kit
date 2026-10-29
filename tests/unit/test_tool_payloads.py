"""Tool JSON validation has explicit timing before and after external side effects."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from agent_runtime.errors import ToolExecutionError, ToolValidationError
from agent_runtime.hooks import HookEvent, HookManager
from agent_runtime.models import RuntimeContext, ToolCall, ToolResult, ToolResultContent
from agent_runtime.tools import ToolRegistry


class PayloadInput(BaseModel):
    value: Any


def echo(value: object) -> object:
    return value


def invalid_payload(shape: str) -> object:
    if shape == "cycle":
        value: list[object] = []
        value.append(value)
        return value
    return {
        "nan": {"nested": [float("nan")]},
        "infinity": float("inf"),
        "negative-infinity": float("-inf"),
        "tuple": (1, 2),
        "set": {1, 2},
        "bytes": b"PRIVATE-SENTINEL",
        "integer-key": {1: "PRIVATE-SENTINEL"},
        "python-model": PayloadInput(value="PRIVATE-SENTINEL"),
    }[shape]


INVALID_PAYLOADS = (
    "nan",
    "infinity",
    "negative-infinity",
    "tuple",
    "set",
    "bytes",
    "integer-key",
    "python-model",
    "cycle",
)


@pytest.mark.parametrize("shape", INVALID_PAYLOADS)
@pytest.mark.parametrize("boundary", ["call", "result", "message"])
def test_canonical_tool_payloads_reject_non_json_values(shape: str, boundary: str) -> None:
    value = invalid_payload(shape)
    with pytest.raises(ValidationError):
        if boundary == "call":
            ToolCall(id="call-1", name="lookup", arguments={"value": value})
        elif boundary == "result":
            ToolResult(tool_call_id="call-1", tool_name="lookup", status="succeeded", output=value)
        else:
            ToolResultContent(tool_call_id="call-1", result=value)


@pytest.mark.parametrize("shape", INVALID_PAYLOADS)
async def test_invalid_registry_arguments_never_reach_handler_or_hooks(shape: str) -> None:
    observed: list[str] = []

    class Audit:
        name = "audit"

        async def handle(self, event: HookEvent) -> None:
            observed.append(event.phase)

    def handler(value: object) -> object:
        observed.append("handler")
        return value

    registry = ToolRegistry(hooks=HookManager([Audit()]))
    registry.register(
        name="lookup", description="Synthetic tool", input_model=PayloadInput, handler=handler
    )
    with pytest.raises(ToolValidationError) as caught:
        await registry.invoke(
            "lookup",
            {"value": invalid_payload(shape)},
            tool_call_id="call-1",
            context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        )
    assert "PRIVATE-SENTINEL" not in caught.value.public_message
    assert observed == []


@pytest.mark.parametrize("shape", INVALID_PAYLOADS)
async def test_invalid_output_fails_after_handler_but_before_after_tool_hooks(shape: str) -> None:
    observed: list[str] = []

    class Audit:
        name = "audit"

        async def handle(self, event: HookEvent) -> None:
            observed.append(event.phase)

    def handler(value: object) -> object:
        observed.append("handler")
        return invalid_payload(shape)

    registry = ToolRegistry(hooks=HookManager([Audit()]))
    registry.register(
        name="lookup", description="Synthetic tool", input_model=PayloadInput, handler=handler
    )
    with pytest.raises(ToolExecutionError) as caught:
        await registry.invoke(
            "lookup",
            {"value": None},
            tool_call_id="call-1",
            context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        )
    assert caught.value.retryable is False
    assert "PRIVATE-SENTINEL" not in caught.value.public_message
    # Result validation cannot undo a side effect that already happened in the handler.
    assert observed == ["before_tool", "handler"]


async def test_after_tool_hook_cannot_return_an_invalid_payload() -> None:
    class MutatingHook:
        name = "mutating"

        async def handle(self, event: HookEvent) -> None:
            if event.phase == "after_tool":
                assert event.tool_result is not None
                event.tool_result.output = float("nan")

    registry = ToolRegistry(hooks=HookManager([MutatingHook()]))
    registry.register(
        name="lookup",
        description="Synthetic tool",
        input_model=PayloadInput,
        handler=echo,
    )
    with pytest.raises(ToolExecutionError):
        await registry.invoke(
            "lookup",
            {"value": None},
            tool_call_id="call-1",
            context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        )


async def test_json_payloads_preserve_values_and_shared_acyclic_containers() -> None:
    shared: list[object] = [None, True, False, 0, -3, 1.25, "text", {}, []]
    payload = {"first": shared, "second": shared}
    registry = ToolRegistry()
    registry.register(
        name="lookup",
        description="Synthetic tool",
        input_model=PayloadInput,
        handler=echo,
    )
    result = await registry.invoke("lookup", {"value": payload}, tool_call_id="call-1")
    assert result.output == payload
    assert result.status == "succeeded"
    shared.append("changed after return")
    assert "changed after return" not in result.output["first"]
