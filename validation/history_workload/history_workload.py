"""Separate low-interference history timings from installed-wheel diagnostics."""

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
from psycopg.types.json import set_json_loads
from release_desk.application import ReviewRouter
from release_desk.review import ReviewInput, review_release
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine
from stage_d_fixtures import FrozenSource, clone_turn, message_bytes

import agent_runtime
from agent_runtime import AgentRegistry, AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.context import ContextWindow
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.runs import PostgresRunStore
from agent_runtime.tools import ToolRegistry

ACTIVE = contextvars.ContextVar("history_sample", default=None)
READING = contextvars.ContextVar("history_read", default=False)
MIB = 1024 * 1024


def percentile(values, fraction=0.95):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def host_budgets(report):
    """Keep a completed experiment distinct from satisfying the frozen host requirement."""
    samples = report["samples"]
    single = report["profile"]["concurrency"] == 1
    timing = report["profile"]["lane"] == "timing"
    latency = "end_to_end_seconds"
    if report["profile"]["mode"] == "stream":
        latency = "first_text_seconds"
    return {
        "responsiveness": not (single and timing)
        or percentile([sample[latency] for sample in samples]) <= 2,
        "rss_increase": not single
        or report["rss_measurement_peak_bytes"] - report["rss_before_measurement"] <= 128 * MIB,
        "python_peak": not (single and not timing) or report["python_peak_bytes"] <= 128 * MIB,
        "initial_context": all(sample["context"]["selected_bytes"] <= 65536 for sample in samples),
    }


class DiagnosticMemory(PostgresMemoryStore):
    async def stream_messages(self, thread_id, *, namespace=None):
        source = super().stream_messages(thread_id, namespace=namespace)
        try:
            while True:
                token = READING.set(True)
                start = time.perf_counter()
                try:
                    item = await anext(source)
                except StopAsyncIteration:
                    return
                finally:
                    if (sample := ACTIVE.get()) is not None:
                        sample["history_read_seconds"] += time.perf_counter() - start
                    READING.reset(token)
                if (sample := ACTIVE.get()) is not None:
                    sample["history_rows"] += 1
                yield item
        finally:
            await source.aclose()

    async def get_messages(self, thread_id, *, namespace=None):
        token = READING.set(True)
        start = time.perf_counter()
        try:
            result = await super().get_messages(thread_id, namespace=namespace)
            if (sample := ACTIVE.get()) is not None:
                sample["history_rows"] += len(result)
            return result
        finally:
            if (sample := ACTIVE.get()) is not None:
                sample["history_read_seconds"] += time.perf_counter() - start
            READING.reset(token)

    @staticmethod
    def _message_model(row):
        start = time.perf_counter()
        try:
            return PostgresMemoryStore._message_model(row)
        finally:
            if READING.get() and (sample := ACTIVE.get()) is not None:
                sample["canonical_conversion_seconds"] += time.perf_counter() - start
                sample["canonical_conversion_rows"] += 1


class DiagnosticWindow(ContextWindow):
    async def select_stream(self, history, current):
        sample = ACTIVE.get()
        before = sample["history_read_seconds"] if sample is not None else 0
        start = time.perf_counter()
        try:
            return await super().select_stream(history, current)
        finally:
            if sample is not None:
                # Streaming selection encloses the awaited read. Report its exclusive remainder,
                # not an additive claim that the two overlapping wall intervals are independent.
                sample["context_selection_seconds"] += max(
                    0, time.perf_counter() - start - (sample["history_read_seconds"] - before)
                )

    def select(self, history, current):
        start = time.perf_counter()
        try:
            return super().select(history, current)
        finally:
            if (sample := ACTIVE.get()) is not None:
                sample["context_selection_seconds"] += time.perf_counter() - start


def diagnostic_loads(value):
    start = time.perf_counter()
    try:
        return json.loads(value)
    finally:
        if READING.get() and (sample := ACTIVE.get()) is not None:
            sample["driver_json_decode_seconds"] += time.perf_counter() - start
            sample["driver_json_values"] += 1
            sample["driver_json_bytes"] += len(value)


def before_cursor(conn, cursor, statement, parameters, context, executemany):
    if ACTIVE.get() is not None and READING.get() and "FROM agent_messages" in statement:
        context.history_probe_started = time.perf_counter()


def after_cursor(conn, cursor, statement, parameters, context, executemany):
    if (started := getattr(context, "history_probe_started", None)) is not None:
        sample = ACTIVE.get()
        sample["history_cursor_seconds"] += time.perf_counter() - started
        sample["history_queries"] += 1


class Model:
    def __init__(self, diagnostic):
        self.delegate = ReviewRouter()
        self.calls = 0
        self.diagnostic = diagnostic

    def observe(self, messages):
        self.calls += 1
        if self.diagnostic and (sample := ACTIVE.get()) is not None:
            start = time.perf_counter()
            sample["model_input_bytes"].append(message_bytes(messages))
            sample["probe_serialization_seconds"] += time.perf_counter() - start

    async def generate(self, messages, *, tools=(), context):
        self.observe(messages)
        return await self.delegate.generate(messages, tools=tools, context=context)

    async def stream(self, messages, *, tools=(), context):
        self.observe(messages)
        async with aclosing(self.delegate.stream(messages, tools=tools, context=context)) as stream:
            async for item in stream:
                yield item


class EntryProbe:
    def __init__(self, delegate):
        self.delegate = delegate

    @property
    def descriptor(self):
        return self.delegate.descriptor

    def observe(self, request):
        if (sample := ACTIVE.get()) is not None:
            sample["context"] = dict(request.context.attributes["agent_runtime.context"])
            sample["entry_messages"] = len(request.messages)

    async def invoke(self, request):
        self.observe(request)
        return await self.delegate.invoke(request)

    async def stream(self, request):
        self.observe(request)
        async with aclosing(self.delegate.stream(request)) as stream:
            async for item in stream:
                yield item


async def history_size(engine, thread):
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT count(*), coalesce(sum(octet_length(content::text)),0), "
                "coalesce(max(octet_length(content::text)),0) "
                "FROM agent_messages WHERE thread_id=:thread"
            ),
            {"thread": thread},
        )
        count, content_bytes, largest = result.one()
        assert content_bytes <= 64 * MIB and largest < 2 * MIB
        return {
            "rows": count,
            "content_json_text_bytes": content_bytes,
            "largest_content_bytes": largest,
        }


def safe_plan(value):
    """Retain physical/query metrics, excluding SQL expressions and identifying predicates."""
    allowed = {
        "Node Type",
        "Actual Startup Time",
        "Actual Total Time",
        "Actual Rows",
        "Actual Loops",
        "Shared Hit Blocks",
        "Shared Read Blocks",
        "Temp Read Blocks",
        "Temp Written Blocks",
        "Sort Method",
        "Sort Space Used",
        "Sort Space Type",
        "Planning Time",
        "Execution Time",
        "Plan",
        "Plans",
        "Rows Removed by Filter",
        "Rows Removed by Index Recheck",
    }
    if isinstance(value, list):
        return [safe_plan(item) for item in value]
    if isinstance(value, dict):
        return {key: safe_plan(item) for key, item in value.items() if key in allowed}
    return value


async def experiment(args, report):
    diagnostic = args.lane == "diagnostic"
    assert args.history_turns * 4 <= 40000
    assert args.history_turns * 4 * args.concurrency <= 160000
    identity = f"history-workload-{uuid4()}"
    database = os.environ["WORKLOAD_DATABASE_URL"]
    engine = create_async_engine(
        database,
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=True,
        connect_args={"application_name": identity},
    )
    if diagnostic:
        set_json_loads(diagnostic_loads)
        event.listen(engine.sync_engine, "before_cursor_execute", before_cursor)
        event.listen(engine.sync_engine, "after_cursor_execute", after_cursor)
    memory = (DiagnosticMemory if diagnostic else PostgresMemoryStore)(engine)
    model = Model(diagnostic)
    tools = ToolRegistry()
    tool_calls = 0

    async def inspect_release(repository, tag, distribution):
        nonlocal tool_calls
        tool_calls += 1
        review = await review_release(
            FrozenSource(), ReviewInput(repository=repository, tag=tag, distribution=distribution)
        )
        return {**review.model_dump(mode="json"), "padding": "x" * 1024}

    tools.register(
        name="inspect_release",
        description="Inspect synthetic release metadata",
        input_model=ReviewInput,
        handler=inspect_release,
        timeout_seconds=12,
    )
    registry = AgentRegistry()
    registry.register(
        EntryProbe(
            NativeAgentInvoker(
                agent_id=identity,
                version="stage-e",
                model_client=model,
                tools=tools,
                max_tool_iterations=1,
            )
        )
    )
    runtime = AgentRuntime(
        agents=registry,
        memory=memory,
        runs=PostgresRunStore(engine),
        namespace=identity,
        invocation_timeout_seconds=30,
        context_window=(DiagnosticWindow if diagnostic else ContextWindow)(65536),
        **({"stream_history": True} if getattr(args, "stream_history", False) else {}),
    )
    review = ReviewInput(repository="synthetic/project", tag="v0.5.0")

    def request(thread=None):
        return RuntimeRequest(
            thread_id=thread,
            messages=[Message(role="user", content=[TextContent(text=review.model_dump_json())])],
        )

    initial_tasks = asyncio.all_tasks()
    gate = asyncio.Semaphore(4)
    last = []
    rounds = 2 if args.smoke else (3 if diagnostic else 20 // args.concurrency)
    try:
        async with runtime.lifespan(shutdown_timeout_seconds=6):
            async with engine.connect() as connection:
                report["postgres_version"] = await connection.scalar(text("SHOW server_version"))
            threads = []
            for _ in range(args.concurrency):
                response = await runtime.invoke(identity, request())
                template = [item.message for item in await memory.get_messages(response.thread_id)]
                assert len(template) == 4
                batch = []
                for turn in range(1, args.history_turns):
                    batch.extend(clone_turn(template))
                    if len(batch) >= 100 or turn == args.history_turns - 1:
                        await memory.append_messages(response.thread_id, batch, namespace=identity)
                        batch.clear()
                threads.append(response.thread_id)
            report["seeded_history"] = [await history_size(engine, thread) for thread in threads]
            assert all(item["rows"] == args.history_turns * 4 for item in report["seeded_history"])
            gc.collect()
            report["rss_before_measurement"] = psutil.Process().memory_info().rss
            report["rss_measurement_peak_bytes"] = report["rss_before_measurement"]
            report["phase"] = "measurement"
            if diagnostic:
                tracemalloc.start()

            async def worker(thread):
                for turn in range(rounds):
                    sample = {"turn": turn, "context": None, "first_text_seconds": None}
                    if diagnostic:
                        sample.update(
                            dict.fromkeys(
                                (
                                    "history_read_seconds",
                                    "history_rows",
                                    "canonical_conversion_seconds",
                                    "canonical_conversion_rows",
                                    "context_selection_seconds",
                                    "driver_json_decode_seconds",
                                    "driver_json_values",
                                    "driver_json_bytes",
                                    "history_cursor_seconds",
                                    "history_queries",
                                    "probe_serialization_seconds",
                                ),
                                0,
                            )
                        )
                        sample["model_input_bytes"] = []
                    current, key = request(thread), f"{thread}-{turn}"
                    submitted = time.perf_counter()
                    async with asyncio.timeout(30):
                        await gate.acquire()
                    admitted = time.perf_counter()
                    token = ACTIVE.set(sample)
                    try:
                        if args.mode == "invoke":
                            response = await runtime.invoke(identity, current, idempotency_key=key)
                        else:
                            response = None
                            async with aclosing(
                                runtime.stream(identity, current, idempotency_key=key)
                            ) as stream:
                                async for item in stream:
                                    if (
                                        item.type == "text_delta"
                                        and sample["first_text_seconds"] is None
                                    ):
                                        sample["first_text_seconds"] = (
                                            time.perf_counter() - submitted
                                        )
                                    elif item.type == "completed":
                                        response = item.response
                            assert response is not None
                        finished = time.perf_counter()
                        sample.update(
                            {
                                "end_to_end_seconds": finished - submitted,
                                "service_seconds": finished - admitted,
                                "queue_seconds": admitted - submitted,
                            }
                        )
                        assert sample["context"]["history_messages"] == 4 * (
                            args.history_turns + turn
                        )
                        assert sample["context"]["selected_bytes"] <= 65536
                        if diagnostic:
                            assert (
                                sample["history_rows"]
                                == sample["canonical_conversion_rows"]
                                == 4 * (args.history_turns + turn)
                            )
                            assert sample["driver_json_values"] >= sample["history_rows"]
                            assert sample["history_queries"] >= 1
                            assert len(sample["model_input_bytes"]) == 2
                            assert (
                                sample["model_input_bytes"][0]
                                == sample["context"]["selected_bytes"]
                            )
                        report["samples"].append(sample)
                        if turn == rounds - 1:
                            last.append((current, key, response))
                    finally:
                        ACTIVE.reset(token)
                        gate.release()

            async with asyncio.TaskGroup() as group:
                for thread in threads:
                    group.create_task(worker(thread))
            if diagnostic:
                report["python_current_bytes"], report["python_peak_bytes"] = (
                    tracemalloc.get_traced_memory()
                )
                tracemalloc.stop()
            report["phase"] = "verification"
            counts = model.calls, tool_calls
            for current, key, original in last:
                replay = await runtime.invoke(identity, current, idempotency_key=key)
                assert replay == original
            assert counts == (model.calls, tool_calls)
            report["final_history"] = [await history_size(engine, thread) for thread in threads]
            assert all(
                item["rows"] == 4 * (args.history_turns + rounds)
                for item in report["final_history"]
            )
            if diagnostic:
                async with engine.connect() as connection:
                    plan = await connection.scalar(
                        text(
                            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                            "SELECT id, thread_id, sequence, "
                            "role, content, metadata, created_at FROM agent_messages "
                            "WHERE thread_id=:thread ORDER BY sequence"
                        ),
                        {"thread": threads[0]},
                    )
                report["separate_server_plan"] = safe_plan(plan)
            assert engine.pool.checkedout() == 0
            report["checked_out_connections"] = 0
            report["exact_replay_without_work"] = True
        assert not [task for task in asyncio.all_tasks() - initial_tasks if not task.done()]
        report["surviving_async_tasks"] = 0
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        await engine.dispose()
        if diagnostic:
            set_json_loads(json.loads)
    observer = create_async_engine(database)
    try:
        async with observer.connect() as connection:
            remaining = await connection.scalar(
                text("SELECT count(*) FROM pg_stat_activity WHERE application_name=:name"),
                {"name": identity},
            )
        assert remaining == 0
        report["remaining_database_sessions"] = remaining
    finally:
        await observer.dispose()


async def main(args):
    report = {
        "version": agent_runtime.__version__,
        "stream_history": getattr(args, "stream_history", False),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "profile": {
            key: getattr(args, key)
            for key in ("history_turns", "concurrency", "mode", "lane", "smoke")
        },
        "status": "running",
        "phase": "seed",
        "samples": [],
        "rss_limit_bytes": 1024 * MIB,
        "rss_sampled_peak_bytes": 0,
        "rss_measurement_peak_bytes": 0,
        "python_current_bytes": None,
        "python_peak_bytes": None,
    }
    done, exceeded, failed = threading.Event(), threading.Event(), threading.Event()
    loop, owner = asyncio.get_running_loop(), asyncio.current_task()

    def sample_rss():
        try:
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
        except Exception as exc:
            report["rss_sampler_failure"] = type(exc).__name__
            failed.set()
            loop.call_soon_threadsafe(owner.cancel)

    sampler = threading.Thread(target=sample_rss, name="history-rss", daemon=True)
    started = time.perf_counter()
    sampler.start()
    try:
        async with asyncio.timeout(270):
            await experiment(args, report)
        if failed.is_set():
            raise RuntimeError("RSS sampler failed")
        report["host_budgets"] = host_budgets(report)
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
                "profile": report["profile"],
                "status": report["status"],
                "host_budgets": report["host_budgets"],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-turns", type=int, choices=(10, 100, 1000, 10000), required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 4), required=True)
    parser.add_argument("--mode", choices=("invoke", "stream"), required=True)
    parser.add_argument("--lane", choices=("timing", "diagnostic"), required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--stream-history", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(parser.parse_args()))
