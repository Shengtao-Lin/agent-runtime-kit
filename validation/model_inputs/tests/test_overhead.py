"""Isolated baseline/candidate dispatch measurements with raw observations."""

import json
import os
import time
import tracemalloc
from pathlib import Path
from uuid import uuid4

import pytest
from host_policy import ModelInputBudget, budget_model, canonical_size

from agent_runtime import Message, TextContent
from agent_runtime.models import RuntimeContext
from agent_runtime.models_clients.base import ModelCompleted, ModelResult
from agent_runtime.tools.models import ToolDefinition


class Offline:
    def __init__(self):
        self.result = ModelResult(
            message=Message(role="assistant", content=[TextContent(text="complete")])
        )

    async def generate(self, messages, *, tools=(), context):
        return self.result

    async def stream(self, messages, *, tools=(), context):
        yield ModelCompleted(result=self.result)


@pytest.mark.parametrize("size", [1024, 16384, 65536])
@pytest.mark.parametrize("mode", ["generate", "stream"])
async def test_dispatch_measurement(size, mode):
    settings = json.loads(os.environ["MODEL_INPUT_BENCHMARK"])
    # Several legal blocks keep the near-64-KiB profile under per-block validation limits.
    messages = [Message(role="user", content=[TextContent(text="x") for _ in range(3)])]
    extra = size - canonical_size(messages)
    for index in range(3):
        addition = extra // (3 - index)
        messages[0].content[index].text += "x" * addition
        extra -= addition
    assert canonical_size(messages) == size
    tools = [
        ToolDefinition(
            name="review",
            description="Review a public release",
            input_schema={
                "type": "object",
                "properties": {"repository": {"type": "string"}, "tag": {"type": "string"}},
                "required": ["repository", "tag"],
                "additionalProperties": False,
            },
        )
    ]
    model = Offline()
    if settings["candidate"]:
        model = budget_model(model, ModelInputBudget(65536, 8192))
    context = RuntimeContext(run_id=uuid4(), thread_id=uuid4())

    async def dispatch():
        if mode == "generate":
            await model.generate(messages, tools=tools, context=context)
        else:
            async for _ in model.stream(messages, tools=tools, context=context):
                pass

    for _ in range(50):
        await dispatch()
    report = {**settings, "mode": mode, "message_bytes": size, "tool_bytes": canonical_size(tools)}
    if settings["kind"] == "timing":
        samples = []
        for _ in range(500):
            start = time.perf_counter_ns()
            await dispatch()
            samples.append(time.perf_counter_ns() - start)
        report["nanoseconds"] = samples
    else:
        tracemalloc.start()
        try:
            for _ in range(50):
                await dispatch()
            report["python_peak_bytes"] = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    label = f"{settings['kind']}-{settings['repeat']}-{settings['candidate']}"
    name = f"overhead-{label}-{mode}-{size}.json"
    (Path(os.environ["MODEL_INPUT_REPORTS"]) / name).write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
