"""Use the supported psycopg async event loop on Windows test hosts."""

import asyncio
import sys

import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()
