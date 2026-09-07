"""Typed registration and bounded invocation of local tools."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from agent_runtime.errors import (
    DuplicateRegistrationError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
    ToolValidationError,
)
from agent_runtime.models import ToolResult
from agent_runtime.tools.models import RegisteredTool, ToolDefinition


class ToolRegistry:
    """Registry for schema-declared sync and async tools."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        input_model: type[BaseModel],
        handler: Callable[..., Any],
        timeout_seconds: float = 10.0,
    ) -> ToolDefinition:
        """Register a callable and derive its JSON input schema from Pydantic."""
        if name in self._tools:
            raise DuplicateRegistrationError(f"Tool '{name}' is already registered")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        definition = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_model.model_json_schema(),
        )
        self._tools[name] = RegisteredTool(definition, input_model, handler, timeout_seconds)
        return definition

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return model-visible definitions in stable name order."""
        return tuple(self._tools[name].definition for name in sorted(self._tools))

    async def invoke(
        self, name: str, arguments: dict[str, Any], *, tool_call_id: str
    ) -> ToolResult:
        """Validate and invoke a registered tool within its timeout."""
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Tool '{name}' is not registered") from exc

        try:
            validated = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolValidationError(f"Arguments for tool '{name}' are invalid") from exc

        async def execute() -> Any:
            values = validated.model_dump()
            if inspect.iscoroutinefunction(tool.handler):
                return await tool.handler(**values)
            return await asyncio.to_thread(tool.handler, **values)

        try:
            output = await asyncio.wait_for(execute(), timeout=tool.timeout_seconds)
        except TimeoutError as exc:
            raise ToolTimeoutError(f"Tool '{name}' timed out") from exc
        except Exception as exc:
            if isinstance(exc, ToolTimeoutError):
                raise
            raise ToolExecutionError(f"Tool '{name}' failed") from exc

        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name=name,
            status="succeeded",
            output=output,
        )
