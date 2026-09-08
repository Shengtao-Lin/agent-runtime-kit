"""Local entry point with Windows-compatible async PostgreSQL configuration."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import uvicorn

from agent_runtime.config import RuntimeSettings


def main() -> None:
    """Run the example API bound to localhost by default."""
    parser = argparse.ArgumentParser(description="Run the fictional support-agent API")
    parser.add_argument(
        "--fake-model", action="store_true", help="Run without provider credentials"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-config", default=None)
    arguments = parser.parse_args()
    if arguments.fake_model:
        os.environ["FAKE_MODEL"] = "true"
    settings = RuntimeSettings()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(  # pyright: ignore[reportDeprecated]
            asyncio.WindowsSelectorEventLoopPolicy()
        )
    uvicorn.run(
        "examples.support_agent.api:app",
        host=arguments.host,
        port=arguments.port,
        timeout_graceful_shutdown=settings.shutdown_grace_seconds,
        log_config=arguments.log_config,
    )


if __name__ == "__main__":
    main()
