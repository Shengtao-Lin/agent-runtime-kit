"""Reproduce unprotected dispatch growth without changing the actual v0.7.0 library."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from input_cases import CASES, growth_host


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("mode", ["invoke", "stream"])
async def test_unprotected_growth_is_observed_and_retains_success(case, mode):
    tasks_before = set(asyncio.all_tasks())
    async with growth_host(os.environ["DATABASE_URL"], case) as app:
        request = app.request()
        if mode == "invoke":
            response = await app.runtime.invoke(app.namespace, request, idempotency_key="baseline")
        else:
            events = [
                event
                async for event in app.runtime.stream(
                    app.namespace, request, idempotency_key="baseline"
                )
            ]
            assert events[-1].type == "completed"
            response = events[-1].response
            assert app.model.opened_streams == app.model.closed_streams
        settings = CASES[case]
        assert len(app.model.calls) == settings["rounds"] + 1
        assert len(app.effects) == settings["rounds"] * settings["calls_per_round"]
        assert all(call["initial_selected_bytes"] <= 65536 for call in app.model.calls)
        if case == "tool_definitions":
            assert any(call["tool_bytes"] > 8192 for call in app.model.calls)
        else:
            assert any(call["message_bytes"] > 65536 for call in app.model.calls)
        history = await app.memory.get_messages(response.thread_id, namespace=app.namespace)
        assert len(history) == 2 + settings["rounds"] * (1 + settings["calls_per_round"])
        assert history[0].message == request.messages[0]
        assert (await app.runs.get(response.run_id)).status == "succeeded"
        before = (len(app.model.calls), len(app.effects))
        assert (
            await app.runtime.invoke(app.namespace, request, idempotency_key="baseline") == response
        )
        assert before == (len(app.model.calls), len(app.effects))
    await asyncio.sleep(0)
    assert not (set(asyncio.all_tasks()) - tasks_before)
    assert app.cleanup == {"pool_checkouts": 0, "database_sessions": 0}
    report = {
        "status": "passed",
        "case": case,
        "mode": mode,
        "calls": app.model.calls,
        "tool_effects": len(app.effects),
        "durable_history_messages": len(history),
        "exact_replay_without_work": True,
        "cleanup": app.cleanup,
        "unprotected_budget_violation_observed": True,
    }
    (Path(os.environ["MODEL_INPUT_REPORTS"]) / f"growth-{mode}-{case}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report))
