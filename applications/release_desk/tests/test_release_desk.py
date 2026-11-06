"""Application policy and real installed-library persistence with synthetic GitHub data."""

import asyncio
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from release_desk.application import ReviewRouter, Settings, application
from release_desk.model_inputs import ModelInputExceeded, canonical_size
from release_desk.review import Release, ReviewInput, review_release

import agent_runtime
from agent_runtime.errors import ContextBudgetExceededError, IdempotentRunUnavailableError
from agent_runtime.feedback import FeedbackSubmission
from agent_runtime.memory import PostgresMemoryStore


class Snapshot:
    def __init__(self, *, ready=True):
        self.calls = 0
        self.ready = ready

    async def fetch(self, request):
        self.calls += 1
        return Release(
            tag_name=request.tag,
            draft=False,
            prerelease=False,
            assets=[
                {
                    "name": f"agent_runtime_kit-0.3.0{suffix}",
                    "size": 123,
                    "digest": "sha256:" + "a" * 64,
                }
                for suffix in (["-py3-none-any.whl", ".tar.gz"] if self.ready else [])
            ],
        )


@pytest.mark.parametrize("repository", ["../repo", "owner/..", "https://github.com/owner/repo"])
def test_repository_input_is_a_name_not_a_url(repository):
    with pytest.raises(ValidationError):
        ReviewInput(repository=repository, tag="v0.3.0")


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_application_owned_checklist(ready):
    report = await review_release(
        Snapshot(ready=ready), ReviewInput(repository="synthetic/project", tag="v0.3.0")
    )
    assert report.metadata_ready is ready
    assert "no artifact installation" in report.boundary


def test_consumes_installed_release_not_source_checkout():
    assert Path(agent_runtime.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    distribution = importlib.metadata.distribution("agent-runtime-kit")
    provenance = json.loads(distribution.read_text("direct_url.json"))
    assert provenance["url"].endswith(".whl")
    assert not provenance.get("dir_info", {}).get("editable", False)
    if os.environ.get("RELEASE_DESK_LIBRARY_SOURCE") == "published":
        assert distribution.version == "0.7.0"
        assert provenance["url"] == (
            "https://github.com/Shengtao-Lin/agent-runtime-kit/releases/download/v0.7.0/"
            "agent_runtime_kit-0.7.0-py3-none-any.whl"
        )
        lock = tomllib.loads((Path(__file__).parents[1] / "uv.lock").read_text(encoding="utf-8"))
        package = next(item for item in lock["package"] if item["name"] == "agent-runtime-kit")
        assert package["source"]["url"] == provenance["url"]
        assert package["wheels"][0]["hash"] == (
            "sha256:17464f89c4516b9fc3ef706db54c7b69ea5de4d6dfc67049f650d449464b39cd"
        )
    assert importlib.util.find_spec("examples") is None
    assert importlib.util.find_spec("langchain_core") is None
    assert importlib.util.find_spec("fastapi") is None


@pytest.mark.asyncio
async def test_review_restart_stream_feedback_and_scoped_history():
    url = os.environ.get("RELEASE_DESK_DATABASE_URL")
    if not url:
        pytest.fail("RELEASE_DESK_DATABASE_URL must identify a migrated disposable database")
    settings = Settings(database_url=SecretStr(url), namespace=f"release-desk-test-{uuid4()}")
    review = ReviewInput(repository="synthetic/project", tag="v0.3.0")
    source = Snapshot()
    async with application(settings, source=source) as app:
        first = await app.runtime.invoke(app.agent_id, app.request(review), idempotency_key="first")
        assert first.tool_results[0].output["metadata_ready"] is True
        assert source.calls == 1
        record = await app.feedback.record(
            FeedbackSubmission(
                feedback_id=uuid4(),
                idempotency_key=f"feedback-{settings.namespace}",
                run_id=first.run_id,
                target_type="message",
                target_id=str(first.message.id),
                source="reviewer",
                feedback_type="thumb",
                value=True,
            )
        )
        assert (
            len(await app.memory.get_messages(first.thread_id, namespace=settings.namespace)) == 4
        )
    restarted_source = Snapshot(ready=False)
    async with application(settings, source=restarted_source) as app:
        replay = await app.runtime.invoke(
            app.agent_id, app.request(review), idempotency_key="first"
        )
        assert replay == first and restarted_source.calls == 0
        events = [
            event
            async for event in app.runtime.stream(
                app.agent_id,
                app.request(review, thread_id=first.thread_id),
                idempotency_key="second",
            )
        ]
        assert events[-1].type == "completed"
        assert any(event.type == "text_delta" for event in events)
        assert events[-1].response.tool_results[0].output["metadata_ready"] is False
        assert restarted_source.calls == 1
        assert (
            len(await app.memory.get_messages(first.thread_id, namespace=settings.namespace)) == 8
        )
        assert (await app.feedback.search(run_id=first.run_id)).items == [record]
    environment = {**os.environ, "RELEASE_DESK_NAMESPACE": settings.namespace}
    executable = Path(sys.executable).with_name(
        "release-desk.exe" if sys.platform == "win32" else "release-desk"
    )
    command = [
        str(executable),
        "feedback",
        str(first.run_id),
        str(first.message.id),
        "--key",
        "cli-feedback",
        "--value",
        "helpful",
    ]

    def submit():
        return json.loads(
            subprocess.run(
                command, env=environment, text=True, capture_output=True, check=True
            ).stdout
        )

    submitted = await asyncio.to_thread(submit)
    assert await asyncio.to_thread(submit) == submitted


@pytest.mark.asyncio
async def test_application_body_failure_closes_runtime() -> None:
    from agent_runtime.errors import RuntimeClosingError

    url = os.environ.get("RELEASE_DESK_DATABASE_URL")
    if not url:
        pytest.fail("RELEASE_DESK_DATABASE_URL must identify a migrated disposable database")
    settings = Settings(database_url=SecretStr(url), namespace=f"release-desk-failure-{uuid4()}")
    source = Snapshot()
    with pytest.raises(ValueError, match="application failure"):
        async with application(settings, source=source) as app:
            raise ValueError("application failure")
    with pytest.raises(RuntimeClosingError):
        await app.runtime.invoke(
            app.agent_id,
            app.request(ReviewInput(repository="synthetic/project", tag="v0.4.0")),
        )
    assert source.calls == 0


def test_context_configuration_supports_explicit_budget_and_disable(monkeypatch):
    monkeypatch.delenv("RELEASE_DESK_CONTEXT_MAX_BYTES", raising=False)
    monkeypatch.delenv("RELEASE_DESK_STREAM_HISTORY", raising=False)
    assert Settings(database_url=SecretStr("synthetic")).stream_history is True
    monkeypatch.setenv("RELEASE_DESK_STREAM_HISTORY", "false")
    assert Settings(database_url=SecretStr("synthetic")).stream_history is False
    assert Settings(database_url=SecretStr("synthetic")).context_max_bytes == 65_536
    monkeypatch.setenv("RELEASE_DESK_CONTEXT_MAX_BYTES", "4096")
    assert Settings(database_url=SecretStr("synthetic")).context_max_bytes == 4096
    monkeypatch.setenv("RELEASE_DESK_CONTEXT_MAX_BYTES", "none")
    assert Settings(database_url=SecretStr("synthetic")).context_max_bytes is None
    for value in ("0", "1", "-1"):
        monkeypatch.setenv("RELEASE_DESK_CONTEXT_MAX_BYTES", value)
        with pytest.raises(ValidationError):
            Settings(database_url=SecretStr("synthetic"))


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("stream_history", [False, True])
async def test_repeated_reviews_bound_initial_context_without_losing_history(
    monkeypatch, streaming, stream_history
):
    observed = []
    all_inputs = []
    original = ReviewRouter.generate
    reads = {"materialized": 0, "incremental": 0}
    original_get = PostgresMemoryStore.get_messages
    original_stream = PostgresMemoryStore.stream_messages

    async def get_messages(self, *args, **kwargs):
        reads["materialized"] += 1
        return await original_get(self, *args, **kwargs)

    async def stream_messages(self, *args, **kwargs):
        reads["incremental"] += 1
        source = original_stream(self, *args, **kwargs)
        try:
            async for item in source:
                yield item
        finally:
            await source.aclose()

    monkeypatch.setattr(PostgresMemoryStore, "get_messages", get_messages)
    monkeypatch.setattr(PostgresMemoryStore, "stream_messages", stream_messages)

    async def record(self, messages, **kwargs):
        all_inputs.append((canonical_size(messages), canonical_size(kwargs.get("tools", ()))))
        if messages[-1].role == "user":
            byte_count = len(
                json.dumps(
                    [message.model_dump(mode="json") for message in messages],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            )
            summary = kwargs["context"].attributes["agent_runtime.context"]
            assert summary["selected_bytes"] == byte_count
            observed.append((byte_count, summary["omitted_history_messages"]))
        return await original(self, messages, **kwargs)

    monkeypatch.setattr(ReviewRouter, "generate", record)
    settings = Settings(
        database_url=SecretStr(os.environ["RELEASE_DESK_DATABASE_URL"]),
        namespace=f"release-desk-context-{uuid4()}",
        context_max_bytes=65536,
        stream_history=stream_history,
    )
    review = ReviewInput(repository="synthetic/project", tag="v0.3.0")
    source = Snapshot()
    async with application(settings, source=source) as app:
        thread_id = None
        for turn in range(60):
            current = app.request(review, thread_id=thread_id)
            if streaming:
                events = [
                    event
                    async for event in app.runtime.stream(
                        app.agent_id, current, idempotency_key=str(turn)
                    )
                ]
                assert events[-1].type == "completed"
                response = events[-1].response
            else:
                response = await app.runtime.invoke(
                    app.agent_id, current, idempotency_key=str(turn)
                )
            assert response.tool_results[0].output["metadata_ready"]
            thread_id = response.thread_id
        assert source.calls == 60 and len(observed) == 60
        assert len(all_inputs) == 120
        assert all(messages <= 65536 and tools <= 8192 for messages, tools in all_inputs)
        assert reads == {
            "materialized": 0 if stream_history else 60,
            "incremental": 60 if stream_history else 0,
        }
        assert max(size for size, _ in observed) <= 65536
        assert any(omitted > 0 for _, omitted in observed)
        history = await app.memory.get_messages(thread_id, namespace=settings.namespace)
        assert len(history) == 240
        assert [item.message.role for item in history] == [
            "user",
            "assistant",
            "tool",
            "assistant",
        ] * 60
    # Changing both the window and read mode does not reexecute the successful key.
    async with application(
        settings.model_copy(
            update={
                "context_max_bytes": 2,
                "stream_history": not stream_history,
                "model_input_max_bytes": 2,
                "model_tools_max_bytes": 2,
            }
        ),
        source=source,
    ) as app:
        before_replay = reads.copy()
        replay = await app.runtime.invoke(app.agent_id, current, idempotency_key="59")
        assert replay == response and source.calls == 60
        assert len(all_inputs) == 120
        assert reads == before_replay
        with pytest.raises(ContextBudgetExceededError):
            await app.runtime.invoke(
                app.agent_id, app.request(review, thread_id=thread_id), idempotency_key="overflow"
            )
        assert source.calls == 60
        expected_reads = before_replay.copy()
        expected_reads["materialized" if stream_history else "incremental"] += 1
        assert reads == expected_reads
        assert len(await app.memory.get_messages(thread_id, namespace=settings.namespace)) == 240


def test_model_input_configuration_and_disable(monkeypatch):
    monkeypatch.delenv("RELEASE_DESK_MODEL_INPUT_MAX_BYTES", raising=False)
    monkeypatch.delenv("RELEASE_DESK_MODEL_TOOLS_MAX_BYTES", raising=False)
    settings = Settings(database_url=SecretStr("synthetic"))
    assert settings.model_input_max_bytes == 65536 and settings.model_tools_max_bytes == 8192
    monkeypatch.setenv("RELEASE_DESK_MODEL_INPUT_MAX_BYTES", "none")
    assert Settings(database_url=SecretStr("synthetic")).model_input_max_bytes is None
    for name in ("RELEASE_DESK_MODEL_INPUT_MAX_BYTES", "RELEASE_DESK_MODEL_TOOLS_MAX_BYTES"):
        for value in ("1", "0", "-1"):
            monkeypatch.setenv(name, value)
            with pytest.raises(ValidationError):
                Settings(database_url=SecretStr("synthetic"))
        monkeypatch.delenv(name)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "message_budget,tool_budget,expected_calls",
    [(2, 8192, 0), (500, 8192, 1), (65536, 2, 0), (None, 2, 2)],
)
async def test_application_dispatch_budget_and_deliberate_disable(
    monkeypatch, streaming, message_budget, tool_budget, expected_calls
):
    calls = []
    original = ReviewRouter.generate

    async def record(self, messages, **kwargs):
        calls.append(canonical_size(messages))
        return await original(self, messages, **kwargs)

    monkeypatch.setattr(ReviewRouter, "generate", record)
    settings = Settings(
        database_url=SecretStr(os.environ["RELEASE_DESK_DATABASE_URL"]),
        namespace=f"release-desk-model-budget-{uuid4()}",
        model_input_max_bytes=message_budget,
        model_tools_max_bytes=tool_budget,
    )
    source = Snapshot()
    async with application(settings, source=source) as app:
        thread = await app.memory.create_thread(namespace=settings.namespace)
        request = app.request(
            ReviewInput(repository="synthetic/project", tag="v0.3.0"), thread_id=thread.id
        )

        async def execute():
            if streaming:
                return [
                    event
                    async for event in app.runtime.stream(
                        app.agent_id, request, idempotency_key="guard"
                    )
                ][-1].response
            return await app.runtime.invoke(app.agent_id, request, idempotency_key="guard")

        if message_budget is None:
            response = await execute()
            assert (
                await app.runtime.invoke(app.agent_id, request, idempotency_key="guard") == response
            )
            assert len(await app.memory.get_messages(thread.id, namespace=settings.namespace)) == 4
        else:
            with pytest.raises(ModelInputExceeded):
                await execute()
            assert not await app.memory.get_messages(thread.id, namespace=settings.namespace)
            with pytest.raises(IdempotentRunUnavailableError):
                await app.runtime.invoke(app.agent_id, request, idempotency_key="guard")
        assert len(calls) == expected_calls
        assert source.calls == (1 if expected_calls else 0)


@pytest.mark.asyncio
async def test_disabling_context_uses_complete_history_with_incremental_setting(monkeypatch):
    observed = []
    original = ReviewRouter.generate

    async def record(self, messages, **kwargs):
        if messages[-1].role == "user":
            assert "agent_runtime.context" not in kwargs["context"].attributes
            observed.append(len(messages))
        return await original(self, messages, **kwargs)

    def unexpected_stream(*args, **kwargs):
        pytest.fail("Disabling the context window must disable incremental selection")

    monkeypatch.setattr(ReviewRouter, "generate", record)
    monkeypatch.setattr(PostgresMemoryStore, "stream_messages", unexpected_stream)
    settings = Settings(
        database_url=SecretStr(os.environ["RELEASE_DESK_DATABASE_URL"]),
        namespace=f"release-desk-no-window-{uuid4()}",
        context_max_bytes=None,
        stream_history=True,
    )
    review = ReviewInput(repository="synthetic/project", tag="v0.3.0")
    async with application(settings, source=Snapshot()) as app:
        first = await app.runtime.invoke(app.agent_id, app.request(review), idempotency_key="first")
        await app.runtime.invoke(
            app.agent_id, app.request(review, thread_id=first.thread_id), idempotency_key="second"
        )
        assert observed == [1, 5]
        assert (
            len(await app.memory.get_messages(first.thread_id, namespace=settings.namespace)) == 8
        )
