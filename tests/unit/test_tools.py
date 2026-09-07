import asyncio

import pytest
from pydantic import BaseModel, Field

from agent_runtime.errors import (
    DuplicateRegistrationError,
    ToolExecutionError,
    ToolTimeoutError,
    ToolValidationError,
)
from agent_runtime.tools import ToolRegistry


class AddInput(BaseModel):
    left: int
    right: int = Field(ge=0)


def add_sync(left: int, right: int) -> int:
    return left + right


@pytest.mark.asyncio
async def test_registry_validates_and_invokes_sync_tool() -> None:
    registry = ToolRegistry()
    definition = registry.register(
        name="add",
        description="Add two integers.",
        input_model=AddInput,
        handler=add_sync,
    )

    result = await registry.invoke("add", {"left": 2, "right": 3}, tool_call_id="call-1")

    assert definition.input_schema["type"] == "object"
    assert result.output == 5
    assert result.status == "succeeded"


@pytest.mark.asyncio
async def test_registry_invokes_async_tool() -> None:
    async def add(left: int, right: int) -> int:
        return left + right

    registry = ToolRegistry()
    registry.register(name="add", description="Add.", input_model=AddInput, handler=add)
    result = await registry.invoke("add", {"left": 1, "right": 4}, tool_call_id="call-2")
    assert result.output == 5


@pytest.mark.asyncio
async def test_registry_maps_validation_execution_and_timeout_errors() -> None:
    async def fail(left: int, right: int) -> int:
        del left, right
        raise ValueError("private failure")

    async def wait(left: int, right: int) -> int:
        del left, right
        await asyncio.sleep(0.05)
        return 0

    registry = ToolRegistry()
    registry.register(name="fail", description="Fail.", input_model=AddInput, handler=fail)
    registry.register(
        name="wait", description="Wait.", input_model=AddInput, handler=wait, timeout_seconds=0.001
    )

    with pytest.raises(ToolValidationError):
        await registry.invoke("fail", {"left": 1, "right": -1}, tool_call_id="call-3")
    with pytest.raises(ToolExecutionError):
        await registry.invoke("fail", {"left": 1, "right": 1}, tool_call_id="call-4")
    with pytest.raises(ToolTimeoutError):
        await registry.invoke("wait", {"left": 1, "right": 1}, tool_call_id="call-5")


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry()
    registry.register(name="add", description="Add.", input_model=AddInput, handler=lambda: None)
    with pytest.raises(DuplicateRegistrationError):
        registry.register(
            name="add", description="Add.", input_model=AddInput, handler=lambda: None
        )
