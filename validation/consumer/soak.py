"""Bounded synthetic sustained operation from an installed wheel; no live model traffic."""

from __future__ import annotations

import asyncio
import gc
import json
import os
import platform
import sys
import threading
import time
import tracemalloc
import weakref
from contextlib import aclosing, suppress
from pathlib import Path
from uuid import uuid4

from consumer_app import ConsumerApplication, application, request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import RuntimeResponse, __version__


async def main() -> None:
    duration = int(os.environ.get("CONSUMER_SOAK_SECONDS", "60"))
    concurrency = int(os.environ.get("CONSUMER_SOAK_CONCURRENCY", "4"))
    if not 5 <= duration <= 1800 or not 1 <= concurrency <= 16:
        raise ValueError("Soak duration must be 5..1800 seconds and concurrency 1..16")
    started = time.perf_counter()
    baseline_tasks = asyncio.all_tasks()
    counts = {
        "cycles": 0,
        "turns": 0,
        "tools": 0,
        "streams": 0,
        "replays": 0,
        "cancelled": 0,
        "abandoned": 0,
        "model_calls": 0,
    }
    checkpoints: list[dict[str, object]] = []
    allocation_baseline = None
    engine_probes: list[weakref.ReferenceType[object]] = []
    next_checkpoint = 0.0
    shared_engine = (
        create_async_engine(
            os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"],
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
        )
        if os.environ.get("CONSUMER_SOAK_SHARED_ENGINE") == "1"
        else None
    )
    tracemalloc.start()
    try:
        while time.perf_counter() - started < duration or counts["cycles"] < 3:
            identity = f"soak-{uuid4()}"
            async with application(
                os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"],
                identity,
                invocation_deadline_seconds=20,
                engine=shared_engine,
            ) as app:
                if shared_engine is None and len(engine_probes) < 32:
                    engine_probes.append(weakref.ref(app.engine.sync_engine))
                async with app.runtime.lifespan(shutdown_timeout_seconds=6):
                    replay_request = request("replay")
                    original = await app.runtime.invoke(
                        identity, replay_request, idempotency_key="replay"
                    )

                    async def worker(
                        index: int,
                        identity: str = identity,
                        original: RuntimeResponse = original,
                        app: ConsumerApplication = app,
                    ) -> None:
                        scenario = index % 4
                        if scenario == 0:
                            first = await app.runtime.invoke(identity, request("first"))
                            await app.runtime.invoke(
                                identity, request("continue", thread_id=first.thread_id)
                            )
                            assert len(await app.memory.get_messages(first.thread_id)) == 4
                            counts["turns"] += 2
                        elif scenario == 1:
                            result = await app.runtime.invoke(identity, request("double 7"))
                            assert len(result.tool_results) == 1
                            counts["tools"] += 1
                        elif scenario == 2:
                            deltas = 0
                            completed = False
                            async with aclosing(
                                app.runtime.stream(identity, request("slow stream"))
                            ) as stream:
                                async for event in stream:
                                    if event.type == "text_delta":
                                        deltas += 1
                                        await asyncio.sleep(0.002)
                                    elif event.type == "completed":
                                        completed = True
                            assert deltas == 128 and completed
                            counts["streams"] += 1
                        else:
                            result = await app.runtime.invoke(
                                identity, request("replay"), idempotency_key="replay"
                            )
                            assert result == original
                            counts["replays"] += 1

                    await asyncio.gather(*(worker(index) for index in range(concurrency)))
                    expected_calls = 1 + sum(
                        2 if index % 4 in (0, 1) else 1 if index % 4 == 2 else 0
                        for index in range(concurrency)
                    )
                    assert len(app.model.calls) == expected_calls

                    # Cancel after the fake provider enters, rather than relying on timing.
                    task = asyncio.create_task(
                        app.runtime.invoke(identity, request("wait"), idempotency_key="cancel")
                    )
                    try:
                        await asyncio.wait_for(app.model.started.wait(), timeout=5)
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    cancelled = await app.runs.get_by_idempotency(
                        agent_id=identity, idempotency_key="cancel"
                    )
                    assert cancelled is not None and cancelled.status == "failed"
                    assert cancelled.response is None
                    assert await app.memory.get_messages(cancelled.thread_id) == ()
                    counts["cancelled"] += 1

                    # 128 deltas exceed the runtime queue and exercise backpressure cleanup.
                    async with aclosing(
                        app.runtime.stream(
                            identity, request("slow stream"), idempotency_key="abandon"
                        )
                    ) as stream:
                        async for event in stream:
                            if event.type == "text_delta":
                                break
                    abandoned = await app.runs.get_by_idempotency(
                        agent_id=identity, idempotency_key="abandon"
                    )
                    assert abandoned is not None and abandoned.status == "failed"
                    assert abandoned.response is None
                    assert await app.memory.get_messages(abandoned.thread_id) == ()
                    counts["abandoned"] += 1
                    assert app.engine.sync_engine.pool.checkedout() == 0
                    counts["model_calls"] += len(app.model.calls)
                    # Do not measure the synthetic model's intentional call-history retention.
                    app.model.calls.clear()
                assert app.engine.sync_engine.pool.checkedout() == 0
            assert app.model.closed
            assert app.engine.sync_engine.pool.checkedout() == 0
            del app, original, cancelled, abandoned, task, worker
            await asyncio.sleep(0)
            gc.collect()
            unexpected = {task for task in asyncio.all_tasks() - baseline_tasks if not task.done()}
            assert not unexpected, f"Surviving async tasks after cycle: {len(unexpected)}"
            counts["cycles"] += 1
            current, peak = tracemalloc.get_traced_memory()
            if counts["cycles"] == 3:
                allocation_baseline = tracemalloc.take_snapshot()
            checkpoint = {
                "cycle": counts["cycles"],
                "elapsed_seconds": time.perf_counter() - started,
                "python_current_bytes": current,
                "python_peak_bytes": peak,
                "threads": threading.active_count(),
                "thread_names": sorted(thread.name for thread in threading.enumerate()),
                "surviving_tasks": len(unexpected),
                "checked_out_connections": 0,
                "retained_engine_probes": sum(probe() is not None for probe in engine_probes),
            }
            if checkpoint["elapsed_seconds"] >= next_checkpoint:
                if len(checkpoints) < 32:
                    checkpoints.append(checkpoint)
                else:
                    checkpoints[-1] = checkpoint
                next_checkpoint += duration / 30
        if checkpoints[-1] != checkpoint:
            if len(checkpoints) == 32:
                checkpoints[-1] = checkpoint
            else:
                checkpoints.append(checkpoint)
        allocation_delta = []
        if allocation_baseline is not None:
            allocation_delta = [
                {"location": str(item.traceback), "bytes": item.size_diff, "count": item.count_diff}
                for item in tracemalloc.take_snapshot().compare_to(allocation_baseline, "lineno")[
                    :15
                ]
            ]
    finally:
        tracemalloc.stop()
        if shared_engine is not None:
            await shared_engine.dispose()
    # The runner requires a dedicated disposable database, so other application sessions must end.
    observer = create_async_engine(os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"])
    try:
        remaining_connections = -1
        for _ in range(20):
            async with observer.connect() as connection:
                remaining_connections = int(
                    (
                        await connection.execute(
                            text(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                            )
                        )
                    ).scalar_one()
                )
            if remaining_connections == 0:
                break
            await asyncio.sleep(0.05)
        assert remaining_connections == 0, "Application database sessions survived teardown"
    finally:
        await observer.dispose()
    report = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.system(),
        "requested_seconds": duration,
        "elapsed_seconds": time.perf_counter() - started,
        "concurrency": concurrency,
        "engine_lifecycle": "shared" if shared_engine is not None else "per-cycle",
        "remaining_database_connections": remaining_connections,
        "counts": counts,
        "checkpoints": checkpoints,
        "allocation_delta_top": allocation_delta,
        "memory_scope": "Python allocations, not RSS; informational, no threshold",
        "boundaries": "synthetic native/PostgreSQL; not provider/framework certification",
    }
    await asyncio.to_thread(
        Path(os.environ["CONSUMER_SOAK_REPORT"]).write_text,
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"soak": "passed", "counts": counts}))


if __name__ == "__main__":
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(main())
