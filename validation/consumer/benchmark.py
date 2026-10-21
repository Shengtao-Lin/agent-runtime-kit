"""Reproducible synthetic installed-library timings, not provider-quality measurements."""

import asyncio
import json
import math
import os
import platform
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from uuid import uuid4

from consumer_app import application, request


async def main() -> None:
    count = int(os.environ.get("CONSUMER_BENCHMARK_SAMPLES", "20"))
    concurrency = int(os.environ.get("CONSUMER_BENCHMARK_CONCURRENCY", "4"))
    if not 1 <= count <= 1000 or not 1 <= concurrency <= 32:
        raise ValueError("Samples must be 1..1000 and concurrency 1..32")
    report = {"samples": count, "concurrency": concurrency, "python": platform.python_version()}
    async with application(
        os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"],
        f"benchmark-{uuid4()}",
        invocation_deadline_seconds=30,
    ) as app:
        # Warm connection/model paths before measuring. All measured calls use new threads.
        await app.runtime.invoke(app.agent_id, request("warmup"))
        replay_request = request("replay")
        replay = await app.runtime.invoke(app.agent_id, replay_request, idempotency_key="replay")
        for scenario in ("invoke", "tool", "stream", "slow_stream", "replay"):
            latencies: list[float] = []
            first_deltas: list[float] = []
            gate = asyncio.Semaphore(concurrency)
            calls_before = len(app.model.calls)

            async def sample(
                scenario: str = scenario,
                gate: asyncio.Semaphore = gate,
                latencies: list[float] = latencies,
                first_deltas: list[float] = first_deltas,
            ) -> None:
                async with gate:
                    started = time.perf_counter()
                    if scenario in {"stream", "slow_stream"}:
                        delta_seen = False
                        text_parts: list[str] = []
                        async for event in app.runtime.stream(
                            app.agent_id,
                            request("slow stream" if scenario == "slow_stream" else "stream"),
                        ):
                            if event.type == "text_delta" and not delta_seen:
                                first_deltas.append(time.perf_counter() - started)
                                delta_seen = True
                            if event.type == "text_delta" and scenario == "slow_stream":
                                text_parts.append(event.delta)
                                await asyncio.sleep(0.002)
                        if scenario == "slow_stream":
                            assert len(text_parts) == 128 and "".join(text_parts) == "x" * 1024
                    elif scenario == "replay":
                        assert (
                            await app.runtime.invoke(
                                app.agent_id, replay_request, idempotency_key="replay"
                            )
                            == replay
                        )
                    else:
                        await app.runtime.invoke(
                            app.agent_id, request("double 7" if scenario == "tool" else "hello")
                        )
                    latencies.append(time.perf_counter() - started)

            tracemalloc.start()
            started = time.perf_counter()
            await asyncio.gather(*(sample() for _ in range(count)))
            elapsed = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            ordered = sorted(latencies)
            measured = {
                "p50_ms": statistics.median(ordered) * 1000,
                "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1] * 1000,
                "requests_per_second": count / elapsed,
                "peak_python_bytes": peak,
                "model_calls": len(app.model.calls) - calls_before,
            }
            if first_deltas:
                measured["first_delta_p50_ms"] = statistics.median(first_deltas) * 1000
            if scenario == "slow_stream":
                measured["delta_events_per_request"] = 128
                measured["consumer_delay_ms"] = 2
                assert app.engine.pool.checkedout() == 0
            if scenario == "replay":
                assert measured["model_calls"] == 0
            report[scenario] = measured
        assert await app.runtime.shutdown(timeout_seconds=6)
    await asyncio.to_thread(
        Path(os.environ["CONSUMER_BENCHMARK_REPORT"]).write_text,
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
