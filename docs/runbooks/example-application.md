# Example Application Runbook

## Purpose and Boundaries

The fictional support agent proves that the installable library can compose native, LangChain, and
LangGraph implementations behind one canonical API. It uses synthetic `DEMO-*` data and is not
included in the Python wheel.

Use this application for local exploration, smoke tests, adapter comparison, provider canaries, and
deployment-shape validation. Do not extend it into a production service or use real customer data.

## Prerequisites

- Git and `uv` for local execution.
- Python 3.11 or 3.12.
- Docker Desktop using Linux containers, or Docker Engine with Compose.
- On Windows, the Docker Desktop WSL 2 backend is sufficient; Windows containers are unnecessary.

Fake mode is deterministic and credential-free. Real provider mode additionally needs a provider
API key and outbound HTTPS access.

## Local Quick Start

```bash
uv sync --locked --all-extras --dev
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m examples.support_agent.main --fake-model
```

On Windows, the example entry point configures the Selector event loop required by async Psycopg.
Open a second terminal and run:

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
curl http://127.0.0.1:8000/v1/agents
```

Expected discovery IDs are `support-native`, `support-langchain`, and `support-langgraph`.

## Container Quick Start

```bash
docker compose build app
docker compose run --rm migrate
docker compose up -d --wait app postgres
python scripts/docker_smoke.py
```

The smoke test invokes all three implementations, continues a thread, verifies idempotent replay,
and records feedback. Stop the stack with:

```bash
docker compose down
```

The application image runs as the non-root `agent-runtime` user. Shutdown closes the model client
and database engine, flushes telemetry, and respects the configured grace interval.

## Example Workflows

### Invoke the native implementation

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/agents/support-native/invoke \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-order-42' \
  -d '{"user_id":"synthetic-user","messages":[{"role":"user","content":[{"type":"text","text":"Where is order DEMO-42?"}]}]}'
```

Save the returned `thread_id` and include it in a later request to continue the same conversation.
Repeat the exact request and key to verify durable replay.

### Exercise streaming

```bash
curl -N -X POST http://127.0.0.1:8000/v1/agents/support-native/invoke/stream \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":[{"type":"text","text":"Where is order DEMO-42?"}]}]}'
```

Fake mode emits deterministic events. The terminal `completed` event is the canonical stored
response.

### Exercise the demonstration guardrail

Send a synthetic message containing `demo-blocked-phrase`. The request should be rejected without a
provider call. This verifies hook wiring only; the guardrail is not a production safety policy.

## Real Provider Mode

Set `FAKE_MODEL=false`, choose `MODEL_PROVIDER=openai_compatible`, `anthropic`, or `gemini`, and
provide `MODEL_NAME` and `MODEL_API_KEY`. Set `MODEL_BASE_URL` only when using an approved proxy or
compatible endpoint. Anthropic also uses `MODEL_MAX_TOKENS` and `ANTHROPIC_API_VERSION`.

Before starting real traffic:

1. Use only synthetic `DEMO-*` prompts.
2. Run one non-streaming invocation.
3. Run one streaming invocation and require `completed`.
4. Verify one tool call and its result.
5. Inspect usage, timeout, error, and trace behavior.
6. Return to fake mode after the canary if ongoing provider usage is unnecessary.

## Observability Profile

```bash
docker compose --profile observability up -d app postgres otel-collector jaeger
```

Set the app endpoint to `http://otel-collector:4318/v1/traces`, then open Jaeger at
`http://127.0.0.1:16686`. Leave `OTEL_CAPTURE_CONTENT=false`.

## Common Commands

| Task | Command |
| --- | --- |
| Start only PostgreSQL | `docker compose up -d postgres` |
| Apply migration | `uv run alembic upgrade head` |
| Start fake local app | `uv run python -m examples.support_agent.main --fake-model` |
| Inspect services | `docker compose ps` |
| Inspect app logs | `docker compose logs app` |
| Run smoke test | `python scripts/docker_smoke.py` |
| Stop services | `docker compose down` |

## Troubleshooting

### Migration container fails

Inspect `docker compose logs migrate` and verify its `DATABASE_URL`. Keep the application unready
until the one-shot migration completes successfully.

### App is healthy but not ready

Check PostgreSQL health and compare Alembic current/head revisions. This split is intentional:
liveness represents the process, while readiness represents its ability to serve requests.

### Port collision

Stop the conflicting local process or change the explicit Compose port mapping. If changing the app
port, update smoke-test and curl targets consistently.

### Fake mode works and real provider mode fails

The runtime composition is healthy. Follow the [Model Provider Runbook](model-providers.md) for
secret, model, endpoint, streaming, and tool-schema diagnostics.

### Docker cannot run the image

Confirm Docker is in Linux-container mode and the image architecture is supported. On Windows,
verify Docker Desktop is using the WSL 2 backend and `docker compose version` succeeds.

### Smoke test leaves data behind

The example uses durable PostgreSQL state by design. `docker compose down` stops containers but
retains the named volume; remove data only when you deliberately want a fresh local environment and
have verified the exact non-production volume target.

## Data and Security Notes

The API binds to `127.0.0.1` by default and has no production authentication or tenant isolation.
Use synthetic data only. Do not add real credentials to `.env`, commit them, or bake them into the
image. The example's tools, policies, and operational defaults illustrate integration patterns; they
are not production controls.

## Related Documentation

- [User Runbook](../user-runbook.md)
- [HTTP Service](http-service.md)
- [Model Providers](model-providers.md)
- [Example README](../../examples/support_agent/README.md)
