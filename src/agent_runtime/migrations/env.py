"""Alembic environment shared by installed-wheel and checkout migration commands."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from agent_runtime.memory.tables import Base
from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

explicit_url = config.attributes.get("database_url")
database_url = explicit_url if isinstance(explicit_url, str) else os.environ.get("DATABASE_URL")
if database_url is None:
    # Preserve the checkout's legacy .env/default configuration path. The new CLI
    # and Python API always supply an explicit URL and never reach this fallback.
    from agent_runtime.config import RuntimeSettings

    database_url = RuntimeSettings().database_url.get_secret_value()
target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    assert database_url is not None
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run_async_migrations())
