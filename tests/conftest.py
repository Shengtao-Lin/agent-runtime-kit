"""Shared test configuration."""

import asyncio
import sys


def pytest_sessionstart() -> None:
    """Use the event loop supported by Psycopg async on Windows."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(  # pyright: ignore[reportDeprecated]
            asyncio.WindowsSelectorEventLoopPolicy()
        )
