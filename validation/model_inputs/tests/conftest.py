"""Run the external host probe under Windows-compatible asyncio and an RSS safety ceiling."""

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import psutil
import pytest


def pytest_sessionstart():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def rss_safety(request):
    stopped = threading.Event()
    process = psutil.Process()
    peak = 0
    path = Path(os.environ["MODEL_INPUT_REPORTS"]) / f"safety-{request.node.callspec.id}.json"

    def sample():
        nonlocal peak
        try:
            while not stopped.is_set():
                peak = max(peak, process.memory_info().rss)
                if peak > 1024**3:
                    path.write_text(json.dumps({"status": "rss_abort", "peak_bytes": peak}))
                    os._exit(86)
                stopped.wait(0.01)
        except Exception as exc:
            path.write_text(json.dumps({"status": "sampler_failure", "error": type(exc).__name__}))
            os._exit(87)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        path.write_text(json.dumps({"status": "passed", "peak_bytes": peak, "stopped": True}))
