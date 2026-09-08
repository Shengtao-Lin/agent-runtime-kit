"""Standalone PostgreSQL-backed memory package."""

from typing import TYPE_CHECKING

from agent_runtime.errors import ThreadNotFoundError
from agent_runtime.memory.base import MemoryStore
from agent_runtime.memory.models import MemoryPage, MemoryRecord, StoredMessage, ThreadRecord

if TYPE_CHECKING:
    from agent_runtime.memory.postgres import PostgresMemoryStore

__all__ = [
    "MemoryPage",
    "MemoryRecord",
    "MemoryStore",
    "PostgresMemoryStore",
    "StoredMessage",
    "ThreadNotFoundError",
    "ThreadRecord",
]


def __getattr__(name: str) -> object:
    """Load the optional PostgreSQL implementation only when requested."""
    if name == "PostgresMemoryStore":
        from agent_runtime.memory.postgres import PostgresMemoryStore

        return PostgresMemoryStore
    raise AttributeError(name)
