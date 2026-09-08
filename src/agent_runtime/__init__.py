"""Framework-neutral runtime contracts for AI agents."""

from importlib.metadata import PackageNotFoundError, version

from agent_runtime.models import (
    AgentDescriptor,
    ContentPart,
    InvocationInput,
    InvocationOutput,
    Message,
    RuntimeContext,
    RuntimeRequest,
    RuntimeResponse,
    RuntimeStreamEvent,
    StreamCompleted,
    StreamStarted,
    StreamTextDelta,
    StreamToolResult,
    TextContent,
    ToolCall,
    ToolCallContent,
    ToolResult,
    ToolResultContent,
    Usage,
)
from agent_runtime.registry import AgentRegistry
from agent_runtime.runtime import AgentRuntime

try:
    __version__ = version("agent-runtime-kit")
except PackageNotFoundError:
    __version__ = "0.0.0+uninstalled"

__all__ = [
    "AgentDescriptor",
    "AgentRegistry",
    "AgentRuntime",
    "ContentPart",
    "InvocationInput",
    "InvocationOutput",
    "Message",
    "RuntimeContext",
    "RuntimeRequest",
    "RuntimeResponse",
    "RuntimeStreamEvent",
    "StreamCompleted",
    "StreamStarted",
    "StreamTextDelta",
    "StreamToolResult",
    "TextContent",
    "ToolCall",
    "ToolCallContent",
    "ToolResult",
    "ToolResultContent",
    "Usage",
    "__version__",
]
