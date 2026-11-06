"""Agent framework adapter contracts and implementations."""

from typing import TYPE_CHECKING

from agent_runtime.adapters.base import AgentInvoker, StreamingAgentInvoker

if TYPE_CHECKING:
    from agent_runtime.adapters.langchain import LangChainRunnableAdapter
    from agent_runtime.adapters.langgraph import LangGraphAdapter
    from agent_runtime.adapters.native import NativeAgentInvoker

__all__ = [
    "AgentInvoker",
    "LangChainRunnableAdapter",
    "LangGraphAdapter",
    "NativeAgentInvoker",
    "StreamingAgentInvoker",
]


def __getattr__(name: str) -> object:
    """Load an implementation only when the host explicitly requests it."""
    if name == "LangChainRunnableAdapter":
        from agent_runtime.adapters.langchain import LangChainRunnableAdapter

        return LangChainRunnableAdapter
    if name == "LangGraphAdapter":
        from agent_runtime.adapters.langgraph import LangGraphAdapter

        return LangGraphAdapter
    if name == "NativeAgentInvoker":
        from agent_runtime.adapters.native import NativeAgentInvoker

        return NativeAgentInvoker
    raise AttributeError(name)
