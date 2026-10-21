"""Write synthetic records using the released library before candidate migration."""

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from consumer_app import application, request

from agent_runtime.feedback import FeedbackSubmission


async def main() -> None:
    identity = f"upgrade-{uuid4()}"
    async with application(os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"], identity) as app:
        response = await app.runtime.invoke(
            identity, request("double 7"), idempotency_key="upgrade"
        )
        feedback = await app.feedback.record(
            FeedbackSubmission(
                feedback_id=uuid4(),
                idempotency_key=f"feedback-{identity}",
                run_id=response.run_id,
                target_type="message",
                target_id=str(response.message.id),
                source="reviewer",
                feedback_type="thumb",
                value=True,
            )
        )
        await app.memory.put(
            namespace=identity,
            user_key="synthetic",
            memory_key="language",
            value={"language": "en"},
        )
        state = {
            "identity": identity,
            "response": response.model_dump(mode="json"),
            "feedback_id": str(feedback.feedback_id),
        }
        # Seed an old unowned run, exercising the documented drained-worker upgrade policy.
        legacy_id = uuid4()
        await app.runs.claim(
            run_id=legacy_id,
            request_id=uuid4(),
            agent=response.agent,
            thread_id=response.thread_id,
            idempotency_key="legacy-pending",
            request_hash="legacy-hash",
        )
        await app.runs.transition(legacy_id, "running")
        state["legacy_pending_id"] = str(legacy_id)
        await asyncio.to_thread(
            Path(os.environ["CONSUMER_UPGRADE_STATE"]).write_text,
            json.dumps(state),
            encoding="utf-8",
        )


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
