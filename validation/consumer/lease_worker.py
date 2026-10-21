"""Independent process holding a public store lease until forcibly terminated."""

import asyncio
import os
import sys
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.models import AgentDescriptor
from agent_runtime.runs import PostgresRunStore


async def main() -> None:
    engine = create_async_engine(os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"])
    runs = PostgresRunStore(engine)
    run_id, owner_token = uuid4(), uuid4()
    request = RuntimeRequest(
        thread_id=UUID(sys.argv[2]),
        messages=[Message(role="user", content=[TextContent(text="wait")])],
    )
    try:
        await runs.claim(
            run_id=run_id,
            request_id=request.request_id,
            agent=AgentDescriptor(agent_id=sys.argv[1], version="1", framework="native"),
            thread_id=request.thread_id,
            idempotency_key="lease-worker",
            request_hash=AgentRuntime.request_hash(request),
            owner_token=owner_token,
            lease_seconds=float(sys.argv[3]),
        )
        await runs.transition(run_id, "running", owner_token=owner_token)
        print("RUNNING", flush=True)
        await asyncio.Event().wait()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
