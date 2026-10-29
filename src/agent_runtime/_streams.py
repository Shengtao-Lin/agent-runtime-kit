"""Private stream lifecycle primitives, independent of framework/vendor event types."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar, cast

Event = TypeVar("Event")


async def close_source(source: AsyncIterator[object]) -> None:
    """Close an owned iterator when its consumer stops, fails or is cancelled."""
    close = getattr(source, "aclose", None)
    if callable(close):
        await cast(Callable[[], Awaitable[object]], close)()


async def checked_stream(
    source: AsyncIterator[Event],
    *,
    is_completed: Callable[[Event], bool],
    failure: Callable[[str], Exception],
) -> AsyncIterator[Event]:
    """Withhold terminal success until normal EOF proves no later event exists."""
    terminal: Event | None = None
    completed = False
    try:
        async for event in source:
            if completed:
                raise failure("Stream emitted an event after completion")
            if is_completed(event):
                completed = True
                terminal = event
            else:
                yield event
        if not completed:
            raise failure("Stream ended without completion")
    finally:
        await close_source(source)
    assert terminal is not None
    yield terminal
