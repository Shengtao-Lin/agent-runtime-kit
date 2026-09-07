"""Stable runtime error taxonomy safe for public API translation."""

from __future__ import annotations


class RuntimeKitError(Exception):
    """Base error carrying a stable public code and retry hint."""

    code = "runtime_error"
    retryable = False

    def __init__(self, message: str, *, internal_context: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.public_message = message
        self.internal_context = internal_context or {}


class DuplicateRegistrationError(RuntimeKitError):
    """Raised when a registry name is already in use."""

    code = "duplicate_registration"


class UnknownAgentError(RuntimeKitError):
    """Raised when an agent cannot be resolved."""

    code = "unknown_agent"


class ToolNotFoundError(RuntimeKitError):
    """Raised when a tool cannot be resolved."""

    code = "tool_not_found"


class ToolValidationError(RuntimeKitError):
    """Raised when tool arguments do not match their declared schema."""

    code = "invalid_tool_arguments"


class ToolExecutionError(RuntimeKitError):
    """Raised when registered tool code fails."""

    code = "tool_execution_failed"


class ToolTimeoutError(RuntimeKitError):
    """Raised when tool execution exceeds its configured deadline."""

    code = "tool_timeout"
    retryable = True


class ProviderError(RuntimeKitError):
    """Raised when a model provider fails to produce a canonical result."""

    code = "provider_failure"
    retryable = True


class AgentInvocationError(RuntimeKitError):
    """Raised when a framework adapter cannot invoke or map an agent."""

    code = "agent_invocation_failed"


class MaxToolIterationsError(RuntimeKitError):
    """Raised when a native agent requests tools beyond its configured limit."""

    code = "max_tool_iterations_exceeded"
