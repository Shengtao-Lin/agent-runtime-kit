# Importable Library Integration

## Purpose and ownership

Use this guide to embed the runtime in an application with its own configuration, model/tool
logic and process lifetime. The `agent_runtime` wheel contains shared contracts and execution
policy, not the support-agent or Release Desk business rules. No FastAPI server is required.
`lifespan()` is additive in v0.4.0; v0.3.x hosts keep using explicit `shutdown()`. Check the README's
published status before adopting a development version rather than inferring release from metadata.

## Dependency and startup checklist

1. Install a reviewed wheel with only the extras needed by the host. Keep the application's lock
   separate from the library checkout. Never import `examples` or `applications` as runtime APIs.
2. Read secrets from application-owned settings. Runtime/store/provider construction does not load
   an `.env` file, select a paid model, migrate or provision a database. Explicitly constructing
   the optional convenience `RuntimeSettings` does read environment and `.env`; hosts may supply
   their own settings instead. Importing the library does not construct these settings.
3. Run `python -m agent_runtime.migrations upgrade` as an explicitly authorized one-shot operation
   with the `postgres` extra and `DATABASE_URL`. Drain workers when the upgrade guide requires it.
4. Create one bounded engine/pool and long-lived model client per application lifetime. Check
   connectivity and schema before exposing ingress. Share one persistence domain for memory/runs.
5. Register application tools, hooks and invokers; choose stable namespace/agent/key domains.
6. Enter the runtime lifetime, serve requests, drain ingress, exit the lifetime, then close resources.

## A small application-owned composition function

This function receives the model and database URL from the host. It owns only the engine it creates;
the caller retains ownership of the injected model and optional telemetry. It does not migrate.

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models_clients import ModelClient
from agent_runtime.runs import PostgresRunStore


@asynccontextmanager
async def application(database_url: str, model: ModelClient) -> AsyncIterator[AgentRuntime]:
    engine = create_async_engine(
        database_url, pool_size=2, max_overflow=0, pool_timeout=5, pool_pre_ping=True
    )
    try:
        memory = PostgresMemoryStore(engine)
        if not await memory.check_ready() or not await memory.check_migrations("0002"):
            raise RuntimeError("Database is unavailable or migration is required")
        registry = AgentRegistry()
        registry.register(
            NativeAgentInvoker(agent_id="my-app", version="1.0.0", model_client=model)
        )
        runtime = AgentRuntime(
            agents=registry,
            memory=memory,
            runs=PostgresRunStore(engine),
            namespace="my-app",
        )
        async with runtime.lifespan(shutdown_timeout_seconds=6):
            yield runtime
    finally:
        await engine.dispose()
```

An HTTP host stops/drains ingress before leaving this context. A CLI finishes its requested work
before leaving it. `shutdown()` cancels active invocations; this context is not an ingress-drain
replacement. The engine `finally` is explicit even when startup fails. If shutdown is incomplete,
the example still disposes its owned pool; a production host must additionally supervise surviving
non-cooperative work and inspect durable state, not assume disposal cancels external effects.

## Calls, streaming and retry decisions

Create `RuntimeRequest` from canonical messages, then call `runtime.invoke(agent_id, request,
idempotency_key=...)`. Persist the returned run/thread/message IDs in application state as needed.
Use a fresh key for new semantic work, not for an equivalent request retry. Keys are agent-scoped;
namespace filtering alone does not provide tenant authorization or separate idempotency domains.

For `runtime.stream()`, text deltas are provisional until the durable completed event. Close the
iterator on abandonment, for example with standard-library `contextlib.aclosing` around the stream.
Do not turn visible text into a success when a provider stream fails or a client disconnects.
Successful retries replay without calling the model/tools; failed keys are not silently reexecuted.
An error's `retryable` hint is not an exactly-once side-effect guarantee.

## Resource ownership

| Resource | Owner and shutdown action |
| --- | --- |
| `AgentRuntime` | Host drains ingress, then lifetime exit or checked `shutdown()` |
| Injected SQLAlchemy engine | Host disposes once, after runtime cleanup; stores share it |
| `PostgresMemoryStore.close()` | Disposes its engine, including an injected/shared engine; do not treat as an isolated handle close |
| Provider-created HTTP client | Provider `close()` releases it; host calls close after runtime shutdown |
| Injected provider HTTP client | Provider `close()` does not release it; original owner closes it |
| Telemetry handle/exporter | Owner flushes/shuts down after runtime and dependencies |

Resources are process/event-loop local. Do not reuse engines or async clients across incompatible
loops. On Windows configure a Psycopg-supported Selector loop in the executable host, not by
changing global loop policy during library import. Never print resolved secret-bearing settings.

## Public API and extension boundaries

- Review the [architecture policy matrix](architecture.md#execution-paths-and-policy-coverage):
  runtime hooks bracket an invocation, not each model round, and tool hooks require explicit registry
  wiring. Framework-owned tools do not automatically inherit the native registry's policy.
- Custom model/invoker implementations are trusted extensions. Built-in adapter checks are not
  universal extension validation; current terminal-event/result consistency gaps and their planned
  conformance checks are documented in [the development plan](plan.md).
- Use documented package exports and protocols: runtime/models/registry, `AgentInvoker`,
  `ModelClient`, tools/hooks, memory/run/feedback stores and optional adapter modules.
- Provider modules require their own extras; core import must not load HTTP, database or framework
  implementations. Framework adapters receive canonical history and do not own a second persistent
  memory/checkpointer.
- Underscore-prefixed modules, SQLAlchemy table layout and application/example helpers are not
  extension APIs. Implement documented protocols instead of subclassing private runtime internals.
- Canonical contract `v1`, database schema revision and package version are separate compatibility
  axes. Patch releases preserve documented signatures, with explicit fail-closed correctness fixes;
  pre-1.0 minor releases may change APIs only with release/upgrade guidance. No blanket 1.0 stability
  promise or compatibility with every allowed dependency permutation is implied.

## Verification before adoption

Use a separate venv outside the checkout and check installed provenance. Exercise startup failure,
normal invocation, tools, multi-turn history, success replay after restart, streaming abandonment,
safe errors, feedback and shutdown. Run pytest against real disposable PostgreSQL. Model/proxy
certification is separate and requires approved credentials/budget; ordinary CI remains synthetic.
The [Release Desk runbook](../applications/release_desk/README.md) and
[independent consumer](../validation/consumer/README.md) are executable integration references.
