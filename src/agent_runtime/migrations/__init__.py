"""Explicit schema management using resources shipped with the installed library.

Install the ``postgres`` extra to run migrations. This module remains importable
without optional persistence dependencies.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alembic.config import Config

__all__ = ["current", "heads", "upgrade"]


def _configuration(database_url: str | None = None) -> Config:
    from alembic.config import Config

    config = Config(stdout=sys.stdout, output_buffer=sys.stdout)
    config.set_main_option("script_location", "agent_runtime:migrations")
    # Do not pass URLs through ConfigParser: percent-encoded passwords must stay literal.
    if database_url is not None:
        config.attributes["database_url"] = database_url
    return config


def upgrade(database_url: str, revision: str = "head", *, sql: bool = False) -> None:
    """Upgrade a PostgreSQL schema in a synchronous, application-owned migration job.

    Call outside a running event loop. ``sql=True`` writes migration SQL without
    connecting. Credentials are passed directly, never interpolated by ConfigParser.
    """
    from alembic import command

    command.upgrade(_configuration(database_url), revision, sql=sql)


def current(database_url: str) -> None:
    """Print the target database's current schema revision."""
    from alembic import command

    command.current(_configuration(database_url))


def heads() -> None:
    """Print revisions shipped in the installed wheel without opening a database."""
    from alembic import command

    command.heads(_configuration())
