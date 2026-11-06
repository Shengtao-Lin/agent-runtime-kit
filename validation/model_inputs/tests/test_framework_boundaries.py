"""Real framework control flow with explicit final-mapper/model-boundary placement."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from host_policy import ModelInputExceeded
from input_cases import CASES, ProbeModel, growth_host

from agent_runtime.errors import IdempotentRunUnavailableError


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.parametrize("case", [*CASES, "mapper_growth", "normal"])
async def test_explicit_framework_model_boundary(framework, mode, case):
    await exercise(framework, mode, case, entry_only=False)


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.parametrize("case", ["oversized_tool", "mapper_growth"])
async def test_entry_only_negative_control(framework, mode, case):
    await exercise(framework, mode, case, entry_only=True)


async def exercise(framework, mode, case, *, entry_only):
    tasks_before = set(asyncio.all_tasks())
    settings = CASES.get(
        case, {"rounds": 2 if case == "normal" else 0, "calls_per_round": 1, "payload_bytes": 256}
    )
    delegate = ProbeModel(settings)
    rejected = case != "normal" and not entry_only
    async with growth_host(
        os.environ["DATABASE_URL"],
        case if case in CASES else "oversized_tool",
        protected=True,
        framework=framework,
        entry_only=entry_only,
        mapper_growth=case == "mapper_growth",
        model_stream=mode == "stream",
        model_override=delegate,
    ) as app:
        request = app.request()
        events = []

        async def execute():
            if mode == "invoke":
                return await app.runtime.invoke(app.namespace, request, idempotency_key="framework")
            async for event in app.runtime.stream(
                app.namespace, request, idempotency_key="framework"
            ):
                events.append(event)
            assert events[-1].type == "completed"
            return events[-1].response

        if rejected:
            with pytest.raises(ModelInputExceeded):
                await execute()
        else:
            response = await execute()
        record = await app.runs.get_by_idempotency(
            agent_id=app.namespace, idempotency_key="framework"
        )
        history = await app.memory.get_messages(record.thread_id, namespace=app.namespace)
        work = (len(delegate.calls), len(app.effects))
        if rejected:
            assert len(delegate.calls) == settings["rounds"]
            assert all(
                c["message_bytes"] <= 65536 and c["tool_bytes"] <= 8192 for c in delegate.calls
            )
            assert len(app.effects) == settings["rounds"] * settings["calls_per_round"]
            assert record.status == "failed" and record.error_code == "host_model_input_exceeded"
            assert not history and record.response is None
            assert not any(event.type == "completed" for event in events)
            with pytest.raises(IdempotentRunUnavailableError):
                await app.runtime.invoke(app.namespace, request, idempotency_key="framework")
        else:
            assert record.status == "succeeded"
            assert len(delegate.calls) == settings["rounds"] + 1
            assert len(history) == 2 + settings["rounds"] * (1 + settings["calls_per_round"])
            assert history[0].message == request.messages[0]
            assert (
                await app.runtime.invoke(app.namespace, request, idempotency_key="framework")
                == response
            )
            if entry_only:
                assert any(c["message_bytes"] > 65536 for c in delegate.calls)
        assert work == (len(delegate.calls), len(app.effects))
        assert delegate.opened_streams == delegate.closed_streams
        assert delegate.opened_streams == (len(delegate.calls) if mode == "stream" else 0)
    await asyncio.sleep(0)
    assert not (set(asyncio.all_tasks()) - tasks_before)
    assert app.cleanup == {"pool_checkouts": 0, "database_sessions": 0}
    report = {
        "framework": framework,
        "mode": mode,
        "case": case,
        "entry_only": entry_only,
        "rejected": rejected,
        "actual_calls": delegate.calls,
        "tool_effects": len(app.effects),
        "status": record.status,
        "error_code": record.error_code,
        "history_messages": len(history),
        "cleanup": app.cleanup,
        "exact_replay_or_failed_key_without_work": True,
    }
    label = f"framework-{framework}-{mode}-{case}-{entry_only}.json"
    (Path(os.environ["MODEL_INPUT_REPORTS"]) / label).write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
