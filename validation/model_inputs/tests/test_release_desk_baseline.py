"""Observe every actual offline Release Desk model input without imposing a new policy."""

import asyncio
import importlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from host_policy import ModelInputBudget, budget_model
from pydantic import SecretStr
from sqlalchemy import text

host = importlib.import_module("release_desk.application")
review_module = importlib.import_module("release_desk.review")


def canonical_bytes(values):
    return len(
        json.dumps(
            [value.model_dump(mode="json") for value in values],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


class Snapshot:
    def __init__(self):
        self.calls = 0

    async def fetch(self, request):
        self.calls += 1
        return review_module.Release(
            tag_name=request.tag,
            draft=False,
            prerelease=False,
            assets=[
                {
                    "name": f"agent_runtime_kit-0.7.0{suffix}",
                    "size": 123,
                    "digest": "sha256:" + "a" * 64,
                }
                for suffix in ("-py3-none-any.whl", ".tar.gz")
            ],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.parametrize("protected", [False, True])
async def test_every_release_desk_model_call(monkeypatch, mode, protected):
    calls, engines = [], []
    namespace = f"stage-f-{uuid4()}"
    original_generate = host.ReviewRouter.generate
    original_engine = host.create_async_engine
    original_invoker = host.NativeAgentInvoker
    checks = []
    replay_budget = [65536, 8192]

    class CountedBudget:
        def check(self, messages, tools):
            checks.append((canonical_bytes(messages), canonical_bytes(tools)))
            ModelInputBudget(*replay_budget).check(messages, tools)

    def invoker(**kwargs):
        kwargs["model_client"] = budget_model(kwargs["model_client"], CountedBudget())
        return original_invoker(**kwargs)

    if protected:
        monkeypatch.setattr(host, "NativeAgentInvoker", invoker)

    async def record(self, messages, *, tools=(), context):
        calls.append(
            {
                "index": len(calls),
                "initial": messages[-1].role == "user",
                "message_bytes": canonical_bytes(messages),
                "tool_bytes": canonical_bytes(tools),
                "message_count": len(messages),
                "initial_selected_bytes": context.attributes["agent_runtime.context"][
                    "selected_bytes"
                ],
            }
        )
        return await original_generate(self, messages, tools=tools, context=context)

    def engine(*args, **kwargs):
        instance = original_engine(*args, **kwargs, connect_args={"application_name": namespace})
        engines.append(instance)
        return instance

    monkeypatch.setattr(host.ReviewRouter, "generate", record)
    monkeypatch.setattr(host, "create_async_engine", engine)
    settings = host.Settings(
        database_url=SecretStr(os.environ["DATABASE_URL"]),
        namespace=namespace,
        context_max_bytes=65536,
        stream_history=True,
    )
    review = review_module.ReviewInput(repository="synthetic/project", tag="v0.7.0")
    source = Snapshot()
    tasks_before = set(asyncio.all_tasks())
    async with host.application(settings, source=source) as app:
        thread_id = None
        for turn in range(60):
            request = app.request(review, thread_id=thread_id)
            if mode == "invoke":
                response = await app.runtime.invoke(
                    app.agent_id, request, idempotency_key=str(turn)
                )
            else:
                events = [
                    event
                    async for event in app.runtime.stream(
                        app.agent_id, request, idempotency_key=str(turn)
                    )
                ]
                assert events[-1].type == "completed"
                response = events[-1].response
            thread_id = response.thread_id
        assert len(calls) == 120 and source.calls == 60
        assert all(c["message_bytes"] <= 65536 for c in calls if c["initial"])
        assert all(c["tool_bytes"] <= 8192 for c in calls)
        history = await app.memory.get_messages(thread_id, namespace=namespace)
        assert len(history) == 240
        replay_budget[:] = [2, 2]  # Saved replay must bypass even a newly restrictive policy.
        replay = await app.runtime.invoke(app.agent_id, request, idempotency_key="59")
        assert replay == response and len(calls) == 120 and source.calls == 60
        assert len(checks) == (120 if protected else 0)
        assert engines[0].pool.checkedout() == 0
    assert engines[0].pool.checkedout() == 0
    control = original_engine(os.environ["DATABASE_URL"])
    try:
        async with control.connect() as connection:
            remaining = await connection.scalar(
                text("SELECT count(*) FROM pg_stat_activity WHERE application_name=:name"),
                {"name": namespace},
            )
            postgres = await connection.scalar(text("SHOW server_version"))
    finally:
        await control.dispose()
    assert remaining == 0
    await asyncio.sleep(0)
    assert not (set(asyncio.all_tasks()) - tasks_before)
    report = {
        "status": "passed",
        "protected": protected,
        "mode": mode,
        "turns": 60,
        "calls": calls,
        "message_overflows": sum(c["message_bytes"] > 65536 for c in calls),
        "max_message_bytes": max(c["message_bytes"] for c in calls),
        "complete_history_messages": len(history),
        "exact_replay_without_work": True,
        "remaining_database_sessions": remaining,
        "postgres_version": postgres,
    }
    label = "protected-host" if protected else "baseline"
    (Path(os.environ["MODEL_INPUT_REPORTS"]) / f"{label}-{mode}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in report.items() if key != "calls"}))
