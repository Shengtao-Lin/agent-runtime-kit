"""Model client contracts and provider adapters."""

from agent_runtime.models_clients.base import ModelClient, ModelResult
from agent_runtime.models_clients.fake import DeterministicModelClient

__all__ = ["DeterministicModelClient", "ModelClient", "ModelResult"]
