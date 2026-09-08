"""Native model-and-tool-loop agent implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.adapters.base import (
    AgentCompleted,
    AgentStreamEvent,
    AgentTextDelta,
    AgentToolResult,
)
from agent_runtime.errors import MaxToolIterationsError, ProviderError, RuntimeKitError
from agent_runtime.models import (
    AgentDescriptor,
    InvocationInput,
    InvocationOutput,
    Message,
    ToolResult,
    ToolResultContent,
)
from agent_runtime.models_clients.base import ModelClient, ModelResult, StreamingModelClient
from agent_runtime.telemetry import RuntimeTelemetry
from agent_runtime.tools.registry import ToolRegistry


class NativeAgentInvoker:
    """Run a model with bounded calls to the shared tool registry."""

    def __init__(
        self,
        *,
        agent_id: str,
        version: str,
        model_client: ModelClient,
        tools: ToolRegistry | None = None,
        max_tool_iterations: int = 3,
        capabilities: set[str] | None = None,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        if max_tool_iterations < 1:
            raise ValueError("max_tool_iterations must be positive")
        self._descriptor = AgentDescriptor(
            agent_id=agent_id,
            version=version,
            framework="native",
            capabilities=capabilities or set(),
        )
        self._model_client = model_client
        self._tools = tools or ToolRegistry()
        self._max_tool_iterations = max_tool_iterations
        self._telemetry = telemetry or RuntimeTelemetry()

    @property
    def descriptor(self) -> AgentDescriptor:
        """Return the stable public agent descriptor."""
        return self._descriptor

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        """Generate messages and execute requested tools within a fixed bound."""
        working_messages = list(request.messages)
        generated_messages: list[Message] = []
        tool_results: list[ToolResult] = []

        for iteration in range(self._max_tool_iterations + 1):
            try:
                attributes: dict[str, object] = {
                    "agent.run.id": str(request.context.run_id),
                    "agent.message.count": len(working_messages),
                }
                attributes.update(self._telemetry.content_attributes(working_messages))
                with self._telemetry.span("agent.model.generate", attributes):
                    result = await self._model_client.generate(
                        working_messages,
                        tools=self._tools.definitions(),
                        context=request.context,
                    )
            except RuntimeKitError:
                raise
            except Exception as exc:
                raise ProviderError("Model generation failed") from exc

            generated_messages.append(result.message)
            working_messages.append(result.message)
            if not result.tool_calls:
                return InvocationOutput(
                    messages=generated_messages,
                    tool_results=tool_results,
                    usage=result.usage,
                )
            if iteration == self._max_tool_iterations:
                break

            for tool_call in result.tool_calls:
                tool_result = await self._tools.invoke(
                    tool_call.name,
                    tool_call.arguments,
                    tool_call_id=tool_call.id,
                    context=request.context,
                )
                tool_results.append(tool_result)
                tool_message = Message(
                    role="tool",
                    content=[
                        ToolResultContent(
                            tool_call_id=tool_call.id,
                            result=tool_result.output,
                        )
                    ],
                )
                generated_messages.append(tool_message)
                working_messages.append(tool_message)

        raise MaxToolIterationsError(
            f"Agent exceeded the maximum of {self._max_tool_iterations} tool iterations"
        )

    async def stream(self, request: InvocationInput) -> AsyncIterator[AgentStreamEvent]:
        """Stream provider text while retaining the bounded canonical tool loop."""
        working_messages = list(request.messages)
        generated_messages: list[Message] = []
        tool_results: list[ToolResult] = []

        for iteration in range(self._max_tool_iterations + 1):
            result: ModelResult | None = None
            try:
                attributes: dict[str, object] = {
                    "agent.run.id": str(request.context.run_id),
                    "agent.message.count": len(working_messages),
                }
                attributes.update(self._telemetry.content_attributes(working_messages))
                with self._telemetry.span("agent.model.generate", attributes):
                    if isinstance(self._model_client, StreamingModelClient):
                        async for event in self._model_client.stream(
                            working_messages,
                            tools=self._tools.definitions(),
                            context=request.context,
                        ):
                            if event.type == "text_delta":
                                yield AgentTextDelta(delta=event.delta)
                            else:
                                result = event.result
                    else:
                        result = await self._model_client.generate(
                            working_messages,
                            tools=self._tools.definitions(),
                            context=request.context,
                        )
            except RuntimeKitError:
                raise
            except Exception as exc:
                raise ProviderError("Model generation failed") from exc

            if result is None:
                raise ProviderError("Model stream ended without a completed result")
            generated_messages.append(result.message)
            working_messages.append(result.message)
            if not result.tool_calls:
                yield AgentCompleted(
                    output=InvocationOutput(
                        messages=generated_messages,
                        tool_results=tool_results,
                        usage=result.usage,
                    )
                )
                return
            if iteration == self._max_tool_iterations:
                break

            for tool_call in result.tool_calls:
                tool_result = await self._tools.invoke(
                    tool_call.name,
                    tool_call.arguments,
                    tool_call_id=tool_call.id,
                    context=request.context,
                )
                tool_results.append(tool_result)
                yield AgentToolResult(result=tool_result)
                tool_message = Message(
                    role="tool",
                    content=[
                        ToolResultContent(
                            tool_call_id=tool_call.id,
                            result=tool_result.output,
                        )
                    ],
                )
                generated_messages.append(tool_message)
                working_messages.append(tool_message)

        raise MaxToolIterationsError(
            f"Agent exceeded the maximum of {self._max_tool_iterations} tool iterations"
        )
