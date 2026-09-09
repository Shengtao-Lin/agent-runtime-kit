# Agent Runtime Kit User Runbook

This runbook is the practical guide for installing, integrating, operating, and troubleshooting
Agent Runtime Kit. For design boundaries and rationale, see [Architecture](architecture.md) and the
[architecture decision records](adr/). For a single subsystem, use the
[component runbooks](runbooks/README.md).

## What You Can Do

Agent Runtime Kit can be used as an importable Python library to:

- expose native, LangChain, or LangGraph agents through one canonical contract;
- invoke an agent once or consume canonical streaming events;
- validate and execute typed sync or async tools with deadlines;
- apply ordered lifecycle hooks and guardrails;
- persist threads, messages, long-term records, runs, replayable responses, and feedback in
  PostgreSQL;
- emit OpenTelemetry spans without capturing message content by default; and
- optionally expose discovery, invocation, streaming, feedback, health, and readiness over HTTP.

The repository also includes a fictional support-agent example. The example demonstrates library
composition; it is not included in the Python wheel and is not the product boundary.

## Prerequisites

Choose the prerequisites that match how you plan to use the project:

| Use case | Required |
| --- | --- |
| Core contracts only | Python 3.11 or 3.12 and `uv` |
| Durable runtime | The above plus PostgreSQL 16 and the `postgres` extra |
| HTTP service | The durable runtime plus the `service` extra |
| Local repository development | Git, `uv`, and Docker Desktop or Docker Engine with Compose |
| Windows development | Docker Desktop using Linux containers; the WSL 2 backend is sufficient |

Windows containers are not required. A provider API key is not required for tests or the default
example because both use deterministic or mocked model clients.

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/Shengtao-Lin/agent-runtime-kit.git
cd agent-runtime-kit
uv sync --locked --all-extras --dev
```

To consume a tagged release from another project instead:

```bash
uv add "agent-runtime-kit[postgres,telemetry] @ git+https://github.com/Shengtao-Lin/agent-runtime-kit@v0.1.0"
```

Available extras are `postgres`, `service`, `telemetry`, `adapters`, `openai-compatible`,
`anthropic`, and `gemini`. Install only the integrations your application imports.

### 2. Start PostgreSQL and apply migrations

```bash
docker compose up -d postgres
uv run alembic upgrade head
```

Expected result: `docker compose ps postgres` reports a healthy container and `uv run alembic
current` reports the same revision as `uv run alembic heads`.

### 3. Start the credential-free example

```bash
uv run python -m examples.support_agent.main --fake-model
```

The service binds to `http://127.0.0.1:8000`. In a second terminal:

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
curl http://127.0.0.1:8000/v1/agents
```

Expected result: liveness and readiness succeed, and discovery lists `support-native`,
`support-langchain`, and `support-langgraph`.

### 4. Invoke and stream

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/agents/support-native/invoke \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: runbook-invoke-1' \
  -d '{"user_id":"synthetic-user","messages":[{"role":"user","content":[{"type":"text","text":"Where is order DEMO-42?"}]}]}'
```

The response contains `request_id`, `run_id`, `thread_id`, and the final assistant `message`.
Repeating the same request with the same idempotency key returns the stored response.

```bash
curl -N -X POST http://127.0.0.1:8000/v1/agents/support-native/invoke/stream \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: runbook-stream-1' \
  -d '{"messages":[{"role":"user","content":[{"type":"text","text":"Summarize order DEMO-42"}]}]}'
```

A successful stream starts with `started`, may include `text_delta` and `tool_result`, and ends with
`completed`. Do not treat partial text as a completed response.

## Configuration

The example reads environment variables. Library consumers may supply the corresponding values
through their own configuration layer.

| Variable | Default | Description |
| --- | --- | --- |
| `DATABASE_URL` | Local PostgreSQL URL | Async SQLAlchemy/Psycopg connection URL |
| `DATABASE_POOL_SIZE` | `5` | Persistent connection-pool size |
| `DATABASE_MAX_OVERFLOW` | `5` | Additional bounded connections |
| `DATABASE_POOL_TIMEOUT_SECONDS` | `5` | Pool acquisition and readiness bound |
| `FAKE_MODEL` | `true` | Use the deterministic example model |
| `MODEL_PROVIDER` | `openai_compatible` | `openai_compatible`, `anthropic`, or `gemini` |
| `MODEL_NAME` | `replace-me` | Provider model identifier |
| `MODEL_BASE_URL` | Provider default | Optional provider base URL override |
| `MODEL_API_KEY` | empty | Provider credential used only when fake mode is disabled |
| `MODEL_MAX_TOKENS` | `1024` | Anthropic output-token bound |
| `ANTHROPIC_API_VERSION` | `2023-06-01` | Anthropic Messages API version header |
| `MAX_TOOL_ITERATIONS` | `3` | Maximum native model/tool iterations |
| `TOOL_TIMEOUT_SECONDS` | `10` | Per-tool deadline in the example |
| `INVOCATION_TIMEOUT_SECONDS` | `60` | Total runtime invocation deadline |
| `MAX_REQUEST_BODY_BYTES` | `1000000` | HTTP request body limit |
| `OTEL_SERVICE_NAME` | `agent-runtime-kit` | OpenTelemetry service name |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | Full OTLP HTTP traces endpoint, including `/v1/traces` |
| `OTEL_CAPTURE_CONTENT` | `false` | Permit sensitive message content in spans |
| `SHUTDOWN_GRACE_SECONDS` | `20` | Example shutdown bound |

Keep API keys and production database credentials in the deployment secret store. Do not commit
them to `.env`, include them in container images, or print them during troubleshooting.

## Core Workflows

### Compose the library in your application

Create application-owned model, database, and lifecycle resources, then pass them to the runtime:

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
)

request = RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="Hello")])])
response = await runtime.invoke(
    "my-agent",
    request,
    idempotency_key="request-123",
)
await memory.close()
```

The hosting application owns startup, migration execution, model-client closure, database-engine
closure, and telemetry shutdown.

### Continue a conversation

Use the `thread_id` returned by the first response in the next `RuntimeRequest`. The runtime loads
existing PostgreSQL history before invoking the selected agent. The caller sends only new messages;
framework adapters must return only newly generated messages.

### Retry safely

Use a stable idempotency key for retries of the same semantic request:

| Situation | Action |
| --- | --- |
| Network failed before a response | Retry the same payload with the same key |
| Run is still active | Back off and retry the same key |
| Payload changed | Generate a new key |
| Prior terminal response is replayed | Accept it as the canonical result |
| Key returns `idempotency_conflict` | Stop; the caller reused a key incorrectly |

### Record feedback

Feedback can target a successful run, its assistant message, or one of its tool results. The HTTP
header and body idempotency keys must match. Corrections append a new record with
`supersedes_feedback_id`; existing evidence is never edited.

### Enable real provider traffic

Start with a privacy-reviewed canary. Set `FAKE_MODEL=false`, choose `MODEL_PROVIDER`, and provide
`MODEL_NAME` and `MODEL_API_KEY`. Add `MODEL_BASE_URL` only for a proxy or compatible endpoint.
Verify authentication, non-streaming output, streaming completion, tool calls, timeouts, and usage
mapping before increasing traffic.

### Enable local traces

```bash
docker compose --profile observability up -d postgres otel-collector jaeger
```

For the containerized app, set the endpoint to
`http://otel-collector:4318/v1/traces`. Open Jaeger at `http://127.0.0.1:16686` and search using run,
request, or thread identifiers. Leave `OTEL_CAPTURE_CONTENT=false` unless a reviewed use case
requires content capture.

## Common Commands

| Task | Command |
| --- | --- |
| Install locked dependencies | `uv sync --locked --all-extras --dev` |
| Start PostgreSQL | `docker compose up -d postgres` |
| Apply migrations | `uv run alembic upgrade head` |
| Check migration revision | `uv run alembic current` |
| Run the local example | `uv run python -m examples.support_agent.main --fake-model` |
| Run unit tests | `uv run pytest -m "not integration" -q` |
| Run PostgreSQL tests in PowerShell | `$env:AGENT_RUNTIME_TEST_DATABASE_URL="postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime"; uv run pytest -m integration -q` |
| Run formatting checks | `uv run ruff format --check .` |
| Run lint checks | `uv run ruff check .` |
| Run type checks | `uv run pyright` |
| Build distributions | `uv build` |
| Build the example image | `docker compose build app` |
| Run the container smoke test | `python scripts/docker_smoke.py` |
| Stop local services | `docker compose down` |

`make` targets provide shorter equivalents on environments that support POSIX Make. On Windows,
the direct commands above avoid shell-specific environment-variable syntax.

## Troubleshooting

### Import fails after installation

1. Run `uv run python -c "import agent_runtime; print(agent_runtime.__version__)"`.
2. Confirm the required extra is installed for the module being imported.
3. Confirm the command is running inside the intended `uv` environment.
4. Remember that `examples.support_agent` is intentionally absent from the wheel.

### PostgreSQL is healthy but `/readyz` fails

1. Run `uv run alembic current` and `uv run alembic heads`.
2. Apply migrations with `uv run alembic upgrade head`.
3. Confirm the app and migration command use the same `DATABASE_URL`.
4. Check database permissions and pool acquisition latency.

Do not make readiness ignore a migration mismatch. It protects the runtime from serving against an
incompatible schema.

### Invocation returns 409

- `run_in_progress`: the same request is already running; retry with backoff.
- `idempotency_conflict`: the key was reused with different canonical input; issue a new key.
- a completed replay is not an error; use the stored response.

### Provider mode fails while fake mode works

1. Check the provider selection, model name, base URL, and secret mount.
2. Test a minimal non-streaming request before tools or streaming.
3. Inspect internal provider status and finish metadata without exposing them publicly.
4. For an OpenAI-compatible endpoint, verify its exact tool and streaming behavior; compatibility
   is not uniform.

### A stream disconnects or lacks `completed`

Close the client iterator, treat the attempt as incomplete, and query or retry using the same
idempotency key. If HTTP 200 headers were already sent, inspect the SSE `error` event and internal
spans. Reverse proxies must disable response buffering for SSE.

### Tools repeatedly execute or time out

Confirm tool handlers are idempotent, downstream cancellation is effective, and each tool deadline
is shorter than the total invocation deadline. Repeated calls that exhaust the iteration limit
usually indicate a model/tool schema or result-shape problem; raising the limit is not the first
fix.

### Integration tests are skipped or fail

Start PostgreSQL, apply the migration, and set `AGENT_RUNTIME_TEST_DATABASE_URL` to a disposable
database. All automated tests use pytest; provider unit tests use mocked HTTP transports and need no
credentials.

### Container smoke test cannot connect

Run `docker compose ps` and `docker compose logs app postgres`. Confirm Docker is using Linux
containers, ports `8000` and `5432` are available, and the migration container completed before the
application became ready.

## Data and Security Notes

- PostgreSQL is the only durable backend in v0.1.0. Backups, restore drills, encryption, high
  availability, retention, and cleanup scheduling belong to the deployment.
- Namespace every runtime and memory domain deliberately. A namespace is an application boundary,
  not a substitute for authorization or database tenant isolation.
- The optional HTTP package does not provide authentication, authorization, TLS, public ingress,
  CORS policy, or rate limiting. Add them in the hosting application or platform.
- Default telemetry records identifiers, counts, status, and timing. Enabling content capture may
  export prompts, outputs, or other sensitive text.
- The included phrase and length guardrails demonstrate extension points; they are not a certified
  safety system.
- Use only synthetic `DEMO-*` identifiers with the included support-agent example.

## Getting Help

Before opening an issue, collect:

- the package version and Python version;
- operating system and whether execution is local or containerized;
- the failing command or endpoint and its safe error code;
- relevant run, request, and trace identifiers;
- migration revisions from `alembic current` and `alembic heads`; and
- a minimal reproduction with secrets and customer content removed.

Report reproducible problems in [GitHub Issues](https://github.com/Shengtao-Lin/agent-runtime-kit/issues).
Do not attach API keys, database URLs containing passwords, raw prompts, tool arguments, memory
values, or free-text feedback.

## Related Documentation

- [Component runbooks](runbooks/README.md)
- [Architecture](architecture.md)
- [Project README](../README.md)
- [Architecture decision records](adr/)
- [Changelog](../CHANGELOG.md)
- [Fictional support-agent example](../examples/support_agent/README.md)

## Document History

| Version | Date | Change |
| --- | --- | --- |
| 1.0 | 2026-09-09 | Added an end-to-end user runbook for the v0.1.0 library and example |
