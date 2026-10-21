"""Public managed lifetime from an installed wheel, without owning injected resources."""

from uuid import uuid4

import pytest
from consumer_app import application, request

from agent_runtime import AgentRuntime

pytestmark = pytest.mark.skipif(
    not hasattr(AgentRuntime, "lifespan"), reason="Managed lifespan is additive in v0.4.0"
)


async def test_managed_runtime_leaves_host_database_resources_usable(database_url: str) -> None:
    from agent_runtime.errors import RuntimeClosingError

    async with application(database_url, f"managed-{uuid4()}") as app:
        async with app.runtime.lifespan(shutdown_timeout_seconds=2) as runtime:
            assert runtime is app.runtime
            result = await runtime.invoke(app.agent_id, request("hello"), idempotency_key="first")
            replay = await runtime.invoke(app.agent_id, request("hello"), idempotency_key="first")
            assert result == replay
        with pytest.raises(RuntimeClosingError):
            await app.runtime.invoke(app.agent_id, request("after shutdown"))
        assert len(await app.memory.get_messages(result.thread_id)) == 2
        assert await app.memory.check_ready()
        assert app.engine.sync_engine.pool.checkedout() == 0
        assert await app.runtime.shutdown(timeout_seconds=1)


async def test_managed_lifetime_body_error_preserves_committed_replay(database_url: str) -> None:
    identity = f"managed-restart-{uuid4()}"
    async with application(database_url, identity) as app:
        with pytest.raises(ValueError, match="application failure"):
            async with app.runtime.lifespan(shutdown_timeout_seconds=2):
                result = await app.runtime.invoke(
                    app.agent_id, request("hello"), idempotency_key="key"
                )
                raise ValueError("application failure")
        assert await app.memory.check_ready()
    async with application(database_url, identity) as restarted:
        replay = await restarted.runtime.invoke(
            restarted.agent_id, request("hello"), idempotency_key="key"
        )
        assert replay == result
        assert restarted.model.calls == []
