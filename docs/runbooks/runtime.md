# Runtime Runbook

## Purpose and Ownership

`AgentRuntime` is the durable orchestration boundary. It resolves an invoker, claims a run, loads
thread history, executes lifecycle hooks, enforces the total deadline, persists newly generated
messages, and commits the replayable response. It exposes `invoke()` and `stream()` without leaking
framework or provider objects to callers.

Use this runbook for run state, thread continuity, request replay, terminal persistence, total
timeouts, and canonical stream sequencing. Use the provider, framework, tool, or PostgreSQL
runbook when the runtime has handed control to that boundary.

## Prerequisites

- Install the core package and the persistence implementation used by your application.
- Apply the repository's Alembic migration before accepting traffic.
- Register every public agent before constructing or exposing the application.
- Choose a stable application namespace; do not derive it from untrusted request input.

From a repository checkout:

```bash
uv sync --locked --extra postgres --dev
docker compose up -d postgres
uv run alembic upgrade head
```

## Configuration

| Setting | Example default | Operational meaning |
| --- | --- | --- |
| Runtime namespace | Application-defined | Separates threads and runtime-owned records by application |
| Invocation timeout | `60` seconds | Total bound including history, hooks, provider, tools, and persistence |
| Agent ID | `my-agent` | Stable public routing key and part of idempotency lookup |
| Agent version | `1.0.0` | Observable implementation version; change when behavior changes |
| Idempotency key | Caller-defined | Stable key for retries of the same semantic request |

The example maps `INVOCATION_TIMEOUT_SECONDS` to the runtime timeout. Library consumers pass the
value directly when constructing `AgentRuntime`.

## Core Workflow

```python
from agent_runtime import AgentRegistry, AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.runs import PostgresRunStore
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(database_url, pool_pre_ping=True)
memory = PostgresMemoryStore(engine)
runs = PostgresRunStore(engine)
agents = AgentRegistry()
agents.register(
    NativeAgentInvoker(
        agent_id="my-agent",
        version="1.0.0",
        model_client=model_client,
    )
)
runtime = AgentRuntime(
    agents=agents,
    memory=memory,
    runs=runs,
    namespace="my-application",
    invocation_timeout_seconds=60,
)

request = RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="Hello")])])
response = await runtime.invoke(
    "my-agent",
    request,
    idempotency_key="request-123",
)
```

To continue the conversation, place `response.thread_id` on the next `RuntimeRequest` and send only
the new caller message. The runtime loads prior history. To stream:

```python
async for event in runtime.stream(
    "my-agent",
    request,
    idempotency_key="request-124",
):
    if event.type == "text_delta":
        print(event.delta, end="")
    elif event.type == "completed":
        response = event.response
```

Consume through `completed`. If the caller stops early, close the async iterator so cancellation
can transition the claimed run instead of leaving it active.

## Expected Behavior

| Operation | Expected result |
| --- | --- |
| New request without a thread | A thread and run are created, then the response is persisted |
| Request with an existing thread | Prior messages are loaded before the new messages |
| Equivalent retry with the same key | The stored terminal response is replayed |
| Different request with the same key | `idempotency_conflict` |
| Concurrent retry while the run is active | `run_in_progress` |
| Streaming invoker | `started`, zero or more deltas/results, then `completed` |
| Non-streaming invoker through `stream()` | `started`, then `completed` |

Only the terminal `completed` event contains the durable canonical response. Partial deltas are a
delivery convenience, not a successful stored assistant message.

## Verification and Operational Checks

```bash
uv run pytest tests/integration/test_runtime.py -q
```

- Confirm the requested agent appears in `AgentRegistry.list_descriptors()`.
- Look up the run by agent ID and idempotency key; inspect its categorical status and safe error.
- Confirm the thread exists in the runtime namespace and its message sequence is contiguous.
- Compare the total invocation timeout with downstream provider and tool deadlines.
- Verify a successful response can be replayed after process restart.

## Troubleshooting and Recovery

### `unknown_agent`

Register the exact agent ID before exposing traffic and verify discovery output during startup.
Do not silently route unknown IDs to a default agent.

### `thread_not_found`

The caller supplied stale or foreign state. Reject it and have the caller start a deliberate new
conversation. Never silently create a replacement under the supplied ID.

### `run_in_progress`

Retry the same payload and key with bounded backoff. If the condition persists beyond the maximum
invocation duration plus shutdown grace, inspect the owning process and run transition history.
Do not manually mark the row successful.

### `idempotency_conflict`

Compare the caller's canonical payload generation. A semantically different request needs a new
key. Preserve both sanitized payload hashes when investigating; do not overwrite the stored run.

### `invocation_timeout`

Inspect spans in order: history load, hooks, model calls, tool calls, and terminal persistence.
Shorten or repair the slow dependency before increasing the total bound. Tool and provider
timeouts should fit inside the runtime deadline.

### A stream ends without `completed`

Treat it as incomplete, close the iterator, and resolve the run using the same idempotency key.
Do not persist displayed partial text as a successful assistant response.

## Data and Security Notes

Runtime namespaces reduce accidental record mixing but do not provide authorization or tenant
isolation. Keep public errors categorical, keep internal exception chains in protected logs, and do
not edit durable run rows by hand. The hosting application owns authentication, resource limits,
shutdown ordering, and retention policy.

## Related Documentation

- [User Runbook](../user-runbook.md)
- [Framework Adapters](framework-adapters.md)
- [PostgreSQL](postgres.md)
- [HTTP Service](http-service.md)
