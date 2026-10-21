"""Use the PostgreSQL-compatible Windows event loop."""

import asyncio
import sys


def pytest_sessionstart():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
