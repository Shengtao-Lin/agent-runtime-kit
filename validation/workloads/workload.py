"""Measure Release Desk continuations through installed public library contracts."""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import gc
import json
import math
import os
import platform
import threading
import time
import tracemalloc
from contextlib import aclosing
from pathlib import Path
from uuid import uuid4

import psutil
from release_desk.application import ReviewRouter
from release_desk.review import Release, ReviewInput, review_release
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import agent_runtime
from agent_runtime import AgentRegistry, AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.runs import PostgresRunStore
from agent_runtime.tools import ToolRegistry

ACTIVE = contextvars.ContextVar("workload_sample", default=None)


def message_bytes(messages):
    return len(
        json.dumps(
            [message.model_dump(mode="json") for message in messages],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def record_messages(field, messages):
    sample = ACTIVE.get()
    if sample is not None:
        start = time.perf_counter()
        size = message_bytes(messages)
        sample["measurement_seconds"] += time.perf_counter() - start
        sample[field].append({"messages": len(messages), "json_bytes": size})


class MeasuredMemory(PostgresMemoryStore):
    async def get_messages(self, thread_id, *, namespace=None):
        started = time.perf_counter()
        result = await super().get_messages(thread_id, namespace=namespace)
        sample = ACTIVE.get()
        if sample is not None:
            sample["history_read_seconds"] += time.perf_counter() - started
            record_messages("history", [item.message for item in result])
        return result


class MeasuredModel:
    def __init__(self):
        self.delegate = ReviewRouter()
        self.calls = 0

    async def generate(self, messages, *, tools=(), context):
        self.calls += 1
        record_messages("model_inputs", messages)
        return await self.delegate.generate(messages, tools=tools, context=context)

    async def stream(self, messages, *, tools=(), context):
        self.calls += 1
        record_messages("model_inputs", messages)
        async with aclosing(self.delegate.stream(messages, tools=tools, context=context)) as stream:
            async for event in stream:
                yield event


class MeasuredInvoker:
    def __init__(self, delegate):
        self.delegate = delegate

    @property
    def descriptor(self):
        return self.delegate.descriptor

    async def invoke(self, request):
        record_messages("invoker_inputs", request.messages)
        if (sample := ACTIVE.get()) is not None:
            sample["context_selection"] = request.context.attributes.get("agent_runtime.context")
        return await self.delegate.invoke(request)

    async def stream(self, request):
        record_messages("invoker_inputs", request.messages)
        if (sample := ACTIVE.get()) is not None:
            sample["context_selection"] = request.context.attributes.get("agent_runtime.context")
        async with aclosing(self.delegate.stream(request)) as stream:
            async for event in stream:
                yield event


class FrozenSource:
    async def fetch(self, request):
        return Release(
            tag_name=request.tag,
            draft=False,
            prerelease=False,
            assets=[
                {
                    "name": f"agent_runtime_kit-0.5.0{suffix}",
                    "size": 123,
                    "digest": "sha256:" + "a" * 64,
                }
                for suffix in ("-py3-none-any.whl", ".tar.gz")
            ],
        )


def clone_turn(messages):
    """Preserve canonical call/result links while giving each historical turn new IDs."""
    mapping = {
        part.tool_call.id: f"seed-{uuid4()}"
        for message in messages
        for part in message.content
        if part.type == "tool_call"
    }
    cloned = []
    for message in messages:
        value = message.model_dump(mode="json")
        value["id"] = str(uuid4())
        for part in value["content"]:
            if part["type"] == "tool_call":
                part["tool_call"]["id"] = mapping[part["tool_call"]["id"]]
            elif part["type"] == "tool_result":
                part["tool_call_id"] = mapping[part["tool_call_id"]]
        cloned.append(Message.model_validate(value))
    return cloned


async def experiment(args, report):
    database = os.environ["WORKLOAD_DATABASE_URL"]
    identity = f"workload-{uuid4()}"
    engine = create_async_engine(
        database,
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=True,
        connect_args={"application_name": identity},
    )
    memory = MeasuredMemory(engine)
    model = MeasuredModel()
    tools = ToolRegistry()
    tool_calls = 0

    async def inspect_release(repository, tag, distribution):
        nonlocal tool_calls
        tool_calls += 1
        review = await review_release(
            FrozenSource(), ReviewInput(repository=repository, tag=tag, distribution=distribution)
        )
        return {**review.model_dump(mode="json"), "padding": "x" * (args.padding_kib * 1024)}

    tools.register(
        name="inspect_release",
        description="Inspect synthetic release metadata",
        input_model=ReviewInput,
        handler=inspect_release,
        timeout_seconds=12,
    )
    registry = AgentRegistry()
    registry.register(
        MeasuredInvoker(
            NativeAgentInvoker(
                agent_id=identity,
                version="stage-d",
                model_client=model,
                tools=tools,
                max_tool_iterations=1,
            )
        )
    )
    context_options = {}
    if getattr(args, "context_max_bytes", None) is not None:
        from agent_runtime.context import ContextWindow

        context_options["context_window"] = ContextWindow(args.context_max_bytes)
    runtime = AgentRuntime(
        agents=registry,
        memory=memory,
        runs=PostgresRunStore(engine),
        namespace=identity,
        invocation_timeout_seconds=30,
        **context_options,
    )
    review = ReviewInput(repository="synthetic/project", tag="v0.5.0")

    def request(thread_id=None):
        return RuntimeRequest(
            thread_id=thread_id,
            messages=[Message(role="user", content=[TextContent(text=review.model_dump_json())])],
        )

    initial_tasks = asyncio.all_tasks()
    gate = asyncio.Semaphore(4)
    last_responses = []
    try:
        async with runtime.lifespan(shutdown_timeout_seconds=6):
            threads = []
            for _ in range(args.concurrency):
                first = await runtime.invoke(identity, request())
                template = [item.message for item in await memory.get_messages(first.thread_id)]
                assert len(template) == 4
                batch = []
                for turn in range(1, args.history_turns):
                    batch.extend(clone_turn(template))
                    if len(batch) >= 100 or turn == args.history_turns - 1:
                        await memory.append_messages(first.thread_id, batch, namespace=identity)
                        batch.clear()
                threads.append(first.thread_id)
            gc.collect()
            report["rss_before_measurement"] = psutil.Process().memory_info().rss
            report["phase"] = "measurement"
            tracemalloc.start()
            rounds = 2 if args.smoke else max(2, math.ceil(20 / args.concurrency))

            async def worker(thread_id):
                for turn in range(rounds):
                    submitted = time.perf_counter()
                    sample = {
                        "turn": turn,
                        "mode": args.mode,
                        "measurement_seconds": 0,
                        "history_read_seconds": 0,
                        "history": [],
                        "invoker_inputs": [],
                        "model_inputs": [],
                        "first_text_from_submission_seconds": None,
                        "first_text_from_admission_seconds": None,
                    }
                    async with asyncio.timeout(30):
                        await gate.acquire()
                    admitted = time.perf_counter()
                    token = ACTIVE.set(sample)
                    key = f"{thread_id}-{turn}"
                    invocation = request(thread_id)
                    try:
                        if args.mode == "invoke":
                            response = await runtime.invoke(
                                identity, invocation, idempotency_key=key
                            )
                        else:
                            response = None
                            async with aclosing(
                                runtime.stream(identity, invocation, idempotency_key=key)
                            ) as stream:
                                async for event in stream:
                                    if event.type == "text_delta":
                                        if sample["first_text_from_submission_seconds"] is None:
                                            now = time.perf_counter()
                                            sample["first_text_from_submission_seconds"] = (
                                                now - submitted
                                            )
                                            sample["first_text_from_admission_seconds"] = (
                                                now - admitted
                                            )
                                        if args.mode == "slow_stream":
                                            await asyncio.sleep(0.02)
                                    elif event.type == "completed":
                                        response = event.response
                            assert response is not None
                        ended = time.perf_counter()
                        sample.update(
                            {
                                "queue_seconds": admitted - submitted,
                                "service_seconds": ended - admitted,
                                "end_to_end_seconds": ended - submitted,
                            }
                        )
                        assert len(sample["history"]) == len(sample["invoker_inputs"]) == 1
                        assert len(sample["model_inputs"]) == 2
                        assert sample["history"][0]["messages"] == 4 * (args.history_turns + turn)
                        if context_options:
                            assert (
                                sample["invoker_inputs"][0]["json_bytes"] <= args.context_max_bytes
                            )
                            assert (
                                sample["context_selection"]["selected_bytes"]
                                == sample["invoker_inputs"][0]["json_bytes"]
                            )
                        report["samples"].append(sample)
                        if turn == rounds - 1:
                            last_responses.append((invocation, key, response))
                    finally:
                        ACTIVE.reset(token)
                        gate.release()

            async with asyncio.TaskGroup() as tasks:
                for thread in threads:
                    tasks.create_task(worker(thread))
            report["python_current_bytes"], report["python_peak_bytes"] = (
                tracemalloc.get_traced_memory()
            )
            tracemalloc.stop()
            report["phase"] = "verification"
            before = model.calls, tool_calls
            for invocation, key, response in last_responses:
                replay = await runtime.invoke(identity, invocation, idempotency_key=key)
                assert replay.model_dump(mode="json") == response.model_dump(mode="json")
            assert (model.calls, tool_calls) == before
            for thread in threads:
                assert len(await memory.get_messages(thread)) == 4 * (args.history_turns + rounds)
            assert engine.pool.checkedout() == 0
            report["checked_out_connections"] = 0
            report["exact_replay_without_work"] = True
        assert not [task for task in asyncio.all_tasks() - initial_tasks if not task.done()]
        report["surviving_async_tasks"] = 0
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        await engine.dispose()
    observer = create_async_engine(database)
    try:
        async with observer.connect() as connection:
            remaining = await connection.scalar(
                text("SELECT count(*) FROM pg_stat_activity WHERE application_name = :name"),
                {"name": identity},
            )
            assert remaining == 0
            report["remaining_database_sessions"] = remaining
    finally:
        await observer.dispose()


async def main(args):
    report = {
        "version": agent_runtime.__version__,
        "context_max_bytes": getattr(args, "context_max_bytes", None),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "profile": {
            "history_turns": args.history_turns,
            "concurrency": args.concurrency,
            "padding_kib": args.padding_kib,
            "mode": args.mode,
            "smoke": args.smoke,
        },
        "clock_resolution_seconds": time.get_clock_info("perf_counter").resolution,
        "phase": "seed",
        "samples": [],
        "rss_sampled_peak_bytes": 0,
        "rss_measurement_peak_bytes": 0,
        "rss_limit_bytes": 512 * 1024 * 1024,
        "status": "running",
    }
    done = threading.Event()
    exceeded = threading.Event()
    sampler_failed = threading.Event()
    loop, owner = asyncio.get_running_loop(), asyncio.current_task()

    def collect_rss():
        process = psutil.Process()
        while not done.is_set():
            rss = process.memory_info().rss
            report["rss_sampled_peak_bytes"] = max(report["rss_sampled_peak_bytes"], rss)
            if report["phase"] == "measurement":
                report["rss_measurement_peak_bytes"] = max(
                    report["rss_measurement_peak_bytes"], rss
                )
            if rss > report["rss_limit_bytes"]:
                exceeded.set()
                loop.call_soon_threadsafe(owner.cancel)
                return
            done.wait(0.01)

    def sample_rss():
        try:
            collect_rss()
        except Exception as exc:
            report["rss_sampler_failure"] = type(exc).__name__
            sampler_failed.set()
            loop.call_soon_threadsafe(owner.cancel)

    sampler = threading.Thread(target=sample_rss, name="workload-rss", daemon=True)
    sampler.start()
    started = time.perf_counter()
    try:
        async with asyncio.timeout(180):
            await experiment(args, report)
        if sampler_failed.is_set():
            raise RuntimeError("RSS sampler failed")
        report["status"] = "passed"
    except BaseException as exc:
        report["status"] = "rss_limit" if exceeded.is_set() else "failed"
        report["failure_type"] = type(exc).__name__
        raise
    finally:
        done.set()
        sampler.join(timeout=2)
        report["rss_sampler_stopped"] = not sampler.is_alive()
        report["elapsed_seconds"] = time.perf_counter() - started
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "profile": report["profile"],
                "samples": len(report["samples"]),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-turns", type=int, choices=(1, 10, 100, 1000), required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 4, 16), required=True)
    parser.add_argument("--padding-kib", type=int, choices=(1, 16, 256), required=True)
    parser.add_argument("--mode", choices=("invoke", "stream", "slow_stream"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--context-max-bytes", type=int)
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(parser.parse_args()))
