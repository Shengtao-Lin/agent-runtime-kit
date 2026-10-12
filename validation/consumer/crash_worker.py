"""Child process used only to measure recovery after abrupt process termination."""

import asyncio
import os
import sys

from consumer_app import application, request


async def main() -> None:
    async with application(
        os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"], sys.argv[1], invocation_deadline_seconds=60
    ) as app:
        original = app.model.generate

        async def signal_then_wait(*args: object, **kwargs: object) -> object:
            print("RUNNING", flush=True)
            return await original(*args, **kwargs)

        app.model.generate = signal_then_wait
        await app.runtime.invoke(app.agent_id, request("wait"), idempotency_key="crash")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
