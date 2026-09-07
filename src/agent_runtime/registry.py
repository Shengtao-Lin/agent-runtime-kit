"""Agent registration and discovery."""

from agent_runtime.adapters.base import AgentInvoker
from agent_runtime.errors import DuplicateRegistrationError, UnknownAgentError
from agent_runtime.models import AgentDescriptor


class AgentRegistry:
    """Resolve agent implementations without branching on framework names."""

    def __init__(self) -> None:
        self._invokers: dict[str, AgentInvoker] = {}

    def register(self, invoker: AgentInvoker) -> None:
        """Register an invoker, rejecting duplicate agent identifiers."""
        agent_id = invoker.descriptor.agent_id
        if agent_id in self._invokers:
            raise DuplicateRegistrationError(f"Agent '{agent_id}' is already registered")
        self._invokers[agent_id] = invoker

    def get(self, agent_id: str) -> AgentInvoker:
        """Return a registered invoker or raise a stable not-found error."""
        try:
            return self._invokers[agent_id]
        except KeyError as exc:
            raise UnknownAgentError(f"Agent '{agent_id}' is not registered") from exc

    def list_descriptors(self) -> tuple[AgentDescriptor, ...]:
        """Return descriptors sorted by agent identifier for stable discovery."""
        return tuple(
            invoker.descriptor
            for _, invoker in sorted(self._invokers.items(), key=lambda item: item[0])
        )
