# Agent Runtime Kit

A framework-neutral Python runtime toolkit for building configurable, observable AI agents with
PostgreSQL-backed memory.

> **Project status:** active MVP development. The runtime, PostgreSQL persistence, HTTP service,
> native/LangChain/LangGraph execution, feedback, telemetry, and containerized fake-model example
> are runnable. Authentication and production deployment controls remain application concerns.

## Why this project

Agent applications repeatedly need stable invocation contracts, validated tools, lifecycle policy,
durable memory, idempotency, feedback capture, and telemetry. Agent Runtime Kit owns these shared
runtime concerns without requiring application teams to adopt one orchestration framework.

```mermaid
flowchart TD
    Caller[HTTP or Python caller] --> Runtime[AgentRuntime]
    Runtime --> Registry[AgentRegistry]
    Registry --> Native[Native invoker]
    Registry --> LC[LangChain adapter]
    Registry --> LG[LangGraph adapter]
    Runtime --> Policy[Tools, hooks, guardrails]
    Runtime --> Postgres[(PostgreSQL)]
    Runtime --> OTEL[OpenTelemetry]
```

## Features

- One versioned request, response, message, error, and agent-discovery contract.
- Framework-neutral core with native, LangChain Runnable, and LangGraph adapters.
- PostgreSQL conversation history, long-term memory, runs, idempotency, and feedback.
- Typed sync/async tools with validation, timeouts, and bounded model/tool loops.
- Ordered lifecycle hooks and explicit allow/block guardrail decisions.
- OpenTelemetry spans with content capture disabled by default.
- FastAPI invocation, feedback, liveness, readiness, and discovery endpoints.
- Deterministic fictional example requiring no model credentials.
- Multi-stage non-root application image and explicit migration container.

## Five-minute container quickstart

Prerequisites are Docker Desktop using Linux containers.

```bash
docker compose build app
docker compose run --rm migrate
docker compose up -d app postgres
python scripts/docker_smoke.py
```

The API binds to `127.0.0.1:8000`. The smoke test calls all three agent implementations, continues
a thread, verifies invocation replay, and records feedback. Stop the stack with:

```bash
docker compose down
```

### Manual invocation and feedback

The following example uses `jq` to retain the returned identifiers:

```bash
RESPONSE=$(curl -sS -X POST http://127.0.0.1:8000/v1/agents/support-native/invoke \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: invoke-demo-42' \
  -d '{"user_id":"synthetic-user","messages":[{"role":"user","content":[{"type":"text","text":"Where is order DEMO-42?"}]}]}')

RUN_ID=$(printf '%s' "$RESPONSE" | jq -r .run_id)
MESSAGE_ID=$(printf '%s' "$RESPONSE" | jq -r .message.id)
THREAD_ID=$(printf '%s' "$RESPONSE" | jq -r .thread_id)
FEEDBACK_ID=$(python -c 'import uuid; print(uuid.uuid4())')

curl -sS -X POST http://127.0.0.1:8000/v1/feedback \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: feedback-demo-42' \
  -d "{\"feedback_id\":\"$FEEDBACK_ID\",\"idempotency_key\":\"feedback-demo-42\",\"run_id\":\"$RUN_ID\",\"target_type\":\"message\",\"target_id\":\"$MESSAGE_ID\",\"source\":\"user\",\"feedback_type\":\"thumb\",\"value\":true}"

curl -sS -X POST http://127.0.0.1:8000/v1/agents/support-native/invoke \
  -H 'Content-Type: application/json' \
  -d "{\"thread_id\":\"$THREAD_ID\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"What else can you do?\"}]}]}"
```

## Local Python quickstart

Prerequisites are Python 3.11+, `uv`, and Docker for PostgreSQL.

```bash
uv sync --all-extras --dev
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m examples.support_agent.main --fake-model
```

On Windows, the example entry point configures the Selector event loop required by Psycopg async.

## Library usage

```python
from agent_runtime import AgentRegistry, AgentRuntime, RuntimeRequest
from agent_runtime.models import Message, TextContent

request = RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="Hello")])])
response = await runtime.invoke("my-agent", request, idempotency_key="request-123")
```

All invokers implement the same `AgentInvoker` protocol. LangChain and LangGraph adapters accept
explicit input/output mappers for custom schemas. Their defaults exchange dictionaries containing
canonical `messages`, `metadata`, and `context`. They do not install persistent framework memory or
a LangGraph checkpointer; PostgreSQL history is loaded by `AgentRuntime`.

## Standalone memory usage

```python
from agent_runtime.memory import PostgresMemoryStore

store = PostgresMemoryStore.from_url(
    "postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime"
)
thread = await store.create_thread(namespace="example", user_key="synthetic-user")
await store.put(
    namespace="preferences",
    user_key="synthetic-user",
    memory_key="language",
    value={"language": "en"},
)
await store.close()
```

## HTTP contract

- `POST /v1/agents/{agent_id}/invoke` invokes any registered implementation.
- `POST /v1/feedback` records append-only feedback; `Idempotency-Key` is required.
- `GET /v1/agents` returns IDs, versions, frameworks, and capabilities.
- `GET /healthz` reports process liveness.
- `GET /readyz` checks database connectivity and the expected Alembic revision.

Equivalent invocation retries with the same per-agent `Idempotency-Key` return the original stored
response. Reusing a key with a different canonical request hash returns `409 Conflict`. Feedback
uses the same rule and never modifies prompts, memory, tools, routing, or models.

Every failure uses this safe envelope and omits stack traces and provider/database details:

```json
{
  "request_id": "00000000-0000-0000-0000-000000000000",
  "error": {"code": "validation_error", "message": "Request validation failed", "retryable": false}
}
```

Validation and guardrail failures use 422, unknown resources use 404, idempotency conflicts use
409, dependency failures use 502, and timeouts use 504.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Local PostgreSQL URL | Async SQLAlchemy/Psycopg connection |
| `DATABASE_POOL_SIZE` | `5` | Bounded persistent pool size |
| `DATABASE_MAX_OVERFLOW` | `5` | Additional bounded connections |
| `DATABASE_POOL_TIMEOUT_SECONDS` | `5` | Readiness and pool acquisition bound |
| `FAKE_MODEL` | `true` | Credential-free deterministic mode |
| `MODEL_NAME` | `replace-me` | Real provider model identifier |
| `MODEL_BASE_URL` | OpenAI v1 endpoint | Compatible provider base URL |
| `MODEL_API_KEY` | empty | Real provider bearer credential |
| `MAX_TOOL_ITERATIONS` | `3` | Native tool-loop bound |
| `TOOL_TIMEOUT_SECONDS` | `10` | Deadline applied to each registered example tool |
| `INVOCATION_TIMEOUT_SECONDS` | `60` | Total agent invocation deadline |
| `MAX_REQUEST_BODY_BYTES` | `1000000` | HTTP body limit |
| `OTEL_SERVICE_NAME` | `agent-runtime-kit` | Trace service name |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | OTLP HTTP traces endpoint, including `/v1/traces` |
| `OTEL_CAPTURE_CONTENT` | `false` | Explicitly allow sensitive content in spans |
| `SHUTDOWN_GRACE_SECONDS` | `20` | Graceful shutdown bound used by the example entry point |

## Observability and privacy

Runtime, model, memory, tool, guardrail, and feedback operations emit OpenTelemetry spans. By
default, spans contain stable identifiers, counts, categorical status, and timing—not prompts,
outputs, tool arguments, memory values, free-text feedback, credentials, or database URLs.

Enable the optional local trace viewer with:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces \
  docker compose --profile observability up -d app postgres otel-collector jaeger
```

Open Jaeger at `http://127.0.0.1:16686`. Enabling `OTEL_CAPTURE_CONTENT=true` can export sensitive
data and requires an explicit privacy, retention, and access-control review.

## Development and verification

```bash
make install
make postgres-up
make migrate
make example
make lint
make typecheck
make test
make test-unit
make test-integration
make docker-build
make docker-up
make docker-smoke
make verify
```

All automated tests use pytest; asynchronous cases use pytest-asyncio. The CI workflow separates
quality checks, unit tests on Linux and Windows with supported Python versions, real PostgreSQL
integration tests, and a container smoke test. Jobs use the committed `uv.lock` file. `make test`
and `make verify` expect the local Compose PostgreSQL service and applied migration; override
`TEST_DATABASE_URL` when using another disposable PostgreSQL database.

Tags matching `v*.*.*` start the release workflow only after the same quality and pytest suite
passes against PostgreSQL. It uploads the Python wheel and source distribution as workflow
artifacts, then publishes the versioned container image to GitHub Container Registry. Publishing
to PyPI is intentionally not enabled until trusted publishing is configured for this repository.

## Security and limitations

The example binds to localhost by default. Authentication, authorization, tenant isolation, rate
limiting, TLS termination, network policy, backups, and secret management are deployment
responsibilities outside this MVP. The demonstration guardrails are not a production safety system.

The MVP does not include an evaluation platform, workflow builder, hosted control plane, vector
search, background scheduler, semantic retrieval, streaming, or multiple persistence backends.

See [the architecture notes](docs/architecture.md) and [ADRs](docs/adr) for design rationale.

## Independent project disclaimer

This is an independent personal project built from public documentation and original
implementation. It is not affiliated with, endorsed by, or derived from any employer or client
codebase.

Before publishing, the repository owner should separately review applicable employment,
confidentiality, invention-assignment, and open-source policies.

## License

MIT
