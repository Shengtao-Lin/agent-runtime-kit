"""Canonical tool definitions."""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from agent_runtime.models import StrictModel


class ToolDefinition(StrictModel):
    """Model-visible tool metadata and JSON schema."""

    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
    description: str = Field(min_length=1, max_length=2_000)
    input_schema: Mapping[str, Any]


class RegisteredTool:
    """Internal validated tool registration."""

    def __init__(
        self,
        definition: ToolDefinition,
        input_model: type[BaseModel],
        handler: Any,
        timeout_seconds: float,
    ) -> None:
        self.definition = definition
        self.input_model = input_model
        self.handler = handler
        self.timeout_seconds = timeout_seconds
