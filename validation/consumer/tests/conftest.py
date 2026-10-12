"""Require a migrated disposable PostgreSQL database and isolated wheel environment."""

import asyncio
import os
import sys
from uuid import uuid4

import pytest


def pytest_sessionstart() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(  # pyright: ignore[reportDeprecated]
            asyncio.WindowsSelectorEventLoopPolicy()
        )


@pytest.fixture
def database_url() -> str:
    value = os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL")
    if not value:
        pytest.fail("AGENT_RUNTIME_TEST_DATABASE_URL must identify a disposable migrated database")
    return value


@pytest.fixture
def identity() -> str:
    return f"consumer-{uuid4()}"
