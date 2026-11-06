"""Framework policy applies at explicitly composed runtime/registry boundaries."""

import pytest
from consumer_app import request
from framework_app import application

from agent_runtime.errors import IdempotentRunUnavailableError
from agent_runtime.hooks import GuardrailBlockedError, GuardrailDecision, HookManager

pytestmark = pytest.mark.asyncio


class Policy:
    name = "framework-policy"

    def __init__(self, stop):
        self.stop = stop
        self.phases = []

    async def handle(self, event):
        self.phases.append(event.phase)
        if event.phase == self.stop:
            raise GuardrailBlockedError(GuardrailDecision(allowed=False, reason_code="synthetic"))


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("stop", [None, "before_model", "before_tool", "after_tool", "after_model"])
async def test_composed_framework_policy(database_url, identity, framework, streaming, stop):
    policy = Policy(stop)
    async with application(database_url, identity, framework, hooks=HookManager([policy])) as app:
        observed = []

        async def execute():
            if streaming:
                async for event in app.runtime.stream(
                    identity, request("quote"), idempotency_key="policy"
                ):
                    observed.append(event.type)
            else:
                await app.runtime.invoke(identity, request("quote"), idempotency_key="policy")

        if stop is None:
            await execute()
        else:
            with pytest.raises(GuardrailBlockedError):
                await execute()
        phases = ["before_model", "before_tool", "after_tool", "after_model"]
        expected = phases if stop is None else phases[: phases.index(stop) + 1]
        assert policy.phases == expected
        assert app.tool_calls == ([] if stop in ("before_model", "before_tool") else [3])
        record = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="policy")
        if stop is None:
            assert record.status == "succeeded"
            assert (
                await app.runtime.invoke(identity, request("quote"), idempotency_key="policy")
                == record.response
            )
        else:
            assert record.status == "blocked" and record.error_code == "guardrail_blocked"
            assert record.response is None and "completed" not in observed
            assert await app.memory.get_messages(record.thread_id) == ()
            with pytest.raises(IdempotentRunUnavailableError):
                await app.runtime.invoke(identity, request("quote"), idempotency_key="policy")
        assert policy.phases == expected


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
async def test_framework_native_tools_bypass_registry_policy(database_url, identity, framework):
    policy = Policy("before_tool")
    async with application(
        database_url, identity, framework, hooks=HookManager([policy]), registry_tools=False
    ) as app:
        response = await app.runtime.invoke(identity, request("quote"))
        assert response.message.role == "assistant"
        assert app.tool_calls == [3]
        assert policy.phases == ["before_model", "after_model"]
