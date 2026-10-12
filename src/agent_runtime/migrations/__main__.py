"""Command-line entry point for an explicit migration job."""

from __future__ import annotations

import argparse
import logging
import os

from agent_runtime.migrations import current, heads, upgrade


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the installed Agent Runtime Kit schema.")
    commands = parser.add_subparsers(dest="command", required=True)
    upgrade_parser = commands.add_parser("upgrade", help="Apply migrations to an explicit target")
    upgrade_parser.add_argument("revision", nargs="?", default="head")
    upgrade_parser.add_argument(
        "--sql", action="store_true", help="Generate SQL without connecting"
    )
    commands.add_parser("current", help="Print the target database revision")
    commands.add_parser("heads", help="Print packaged revisions without connecting")
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if args.command != "heads" and not database_url:
        parser.error("DATABASE_URL is required; specify the migration target explicitly")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "heads":
            heads()
        elif args.command == "current" and database_url is not None:
            current(database_url)
        elif database_url is not None:
            upgrade(database_url, args.revision, sql=args.sql)
    except ImportError:
        parser.exit(
            1, "Migration dependencies are unavailable. Install agent-runtime-kit[postgres].\n"
        )
    except Exception:
        # Database exceptions may embed URLs, credentials, or server details.
        parser.exit(
            1, "Migration failed. Check database access, schema permissions, and revision.\n"
        )


if __name__ == "__main__":
    main()
