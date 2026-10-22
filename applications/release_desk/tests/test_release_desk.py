"""Application policy and real installed-library persistence with synthetic GitHub data."""

import asyncio
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from release_desk.application import Settings, application
from release_desk.review import Release, ReviewInput, review_release

import agent_runtime
from agent_runtime.feedback import FeedbackSubmission


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
        assert distribution.version == "0.4.1"
        assert provenance["url"] == (
            "https://github.com/Shengtao-Lin/agent-runtime-kit/releases/download/v0.4.1/"
            "agent_runtime_kit-0.4.1-py3-none-any.whl"
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
