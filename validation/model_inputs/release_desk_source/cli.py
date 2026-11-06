"""Command line workflows for explicit migrations, review, feedback and history."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from uuid import NAMESPACE_URL, UUID, uuid5

from agent_runtime.feedback import FeedbackSubmission
from agent_runtime.migrations import upgrade
from release_desk.application import Settings, application
from release_desk.review import ReviewInput


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Review public Python release metadata read-only")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Explicitly initialize/upgrade the configured database")
    review = commands.add_parser("review")
    review.add_argument("repository", help="Public GitHub owner/repository")
    review.add_argument("tag", help="Stable vMAJOR.MINOR.PATCH tag")
    review.add_argument("--distribution", default="agent-runtime-kit")
    review.add_argument("--key", required=True, help="Stable invocation idempotency key")
    review.add_argument("--thread", type=UUID)
    review.add_argument("--stream", action="store_true", help="Emit canonical events as JSON lines")
    feedback = commands.add_parser("feedback")
    feedback.add_argument("run_id", type=UUID)
    feedback.add_argument("message_id", type=UUID)
    feedback.add_argument("--key", required=True)
    feedback.add_argument("--value", choices=("helpful", "not-helpful"), required=True)
    history = commands.add_parser("history")
    history.add_argument("thread_id", type=UUID)
    return root


async def run(args: argparse.Namespace, settings: Settings) -> int:
    async with application(settings) as app:
        if args.command == "review":
            review = ReviewInput(
                repository=args.repository, tag=args.tag, distribution=args.distribution
            )
            request = app.request(review, thread_id=args.thread)
            if args.stream:
                response = None
                stream = app.runtime.stream(app.agent_id, request, idempotency_key=args.key)
                try:
                    async for event in stream:
                        print(event.model_dump_json(), flush=True)
                        if event.type == "completed":
                            response = event.response
                finally:
                    await stream.aclose()
                if response is None:
                    raise RuntimeError("Review stream did not complete")
            else:
                response = await app.runtime.invoke(app.agent_id, request, idempotency_key=args.key)
                print(response.model_dump_json())
            return 0 if response.tool_results[0].output["metadata_ready"] else 1
        if args.command == "feedback":
            record = await app.feedback.record(
                FeedbackSubmission(
                    feedback_id=uuid5(NAMESPACE_URL, f"release-desk:{app.agent_id}:{args.key}"),
                    idempotency_key=f"{app.agent_id}:{hashlib.sha256(args.key.encode()).hexdigest()}",
                    run_id=args.run_id,
                    target_type="message",
                    target_id=str(args.message_id),
                    source="reviewer",
                    feedback_type="thumb",
                    value=args.value == "helpful",
                )
            )
            print(record.model_dump_json())
            return 0
        messages = await app.memory.get_messages(args.thread_id, namespace=settings.namespace)
        print(json.dumps([item.model_dump(mode="json") for item in messages]))
        return 0


def main() -> None:
    command_line = parser()
    args = command_line.parse_args()
    try:
        settings = Settings()  # pyright: ignore[reportCallIssue]  # Loaded from environment.
        if args.command == "migrate":
            upgrade(settings.database_url.get_secret_value())
            return
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            raise SystemExit(runner.run(run(args, settings)))
    except (KeyboardInterrupt, BrokenPipeError):
        command_line.exit(
            130, "Review interrupted; retry the same key to inspect durable completion.\n"
        )
    except Exception as exc:
        # Never print secret-bearing configuration, chained provider or database errors.
        command_line.exit(
            2,
            f"Release Desk failed ({type(exc).__name__}); check config, migration and key.\n",
        )


if __name__ == "__main__":
    main()
