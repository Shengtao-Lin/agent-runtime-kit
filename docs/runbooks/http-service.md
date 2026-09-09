# HTTP Service Runbook

## Purpose and Ownership

The optional FastAPI package exposes discovery, invocation, SSE streaming, feedback, liveness, and
readiness using the library's canonical Pydantic contracts. It is a reusable transport boundary,
not a complete public gateway.

Authentication, authorization, tenant enforcement, rate limiting, TLS, CORS policy, and public
ingress must be supplied by the hosting application or platform.

## Prerequisites and Composition

Install the `service` extra plus the persistence and other component extras used by the app:

```bash
uv add "agent-runtime-kit[service,postgres] @ git+https://github.com/Shengtao-Lin/agent-runtime-kit@v0.1.0"
```

Construct the runtime, registry, feedback store, and readiness store, then pass them to
`create_app()`:

```python
from agent_runtime.service import create_app

app = create_app(
    runtime=runtime,
    agents=agents,
    feedback=feedback,
    readiness=memory,
    max_request_body_bytes=1_000_000,
    lifespan=lifespan,
)
```

The application lifespan should close model clients and the database engine and flush telemetry.
Run Alembic as a separate deployment job, not inside each web worker.

## Endpoint Reference

| Method and path | Purpose | Success behavior |
| --- | --- | --- |
| `GET /healthz` | Process liveness | `200` while the process can serve HTTP |
| `GET /readyz` | Database and migration readiness | `200` when ready; `503` otherwise |
| `GET /v1/agents` | Agent discovery | Registered IDs, versions, frameworks, capabilities |
| `POST /v1/agents/{agent_id}/invoke` | Canonical invocation | Durable `RuntimeResponse` |
| `POST /v1/agents/{agent_id}/invoke/stream` | Canonical SSE invocation | Events ending in `completed` or `error` |
| `POST /v1/feedback` | Append feedback | `201` with the stored record |

## Start and Verify

From the repository checkout:

```bash
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m examples.support_agent.main --fake-model
```

In another terminal:

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
curl http://127.0.0.1:8000/v1/agents
```

Use `/healthz` for restart decisions and `/readyz` for traffic admission. A database or migration
failure must not make the process appear dead, but it must remove the instance from serving traffic.

## Invocation Workflow

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/agents/support-native/invoke \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: http-runbook-1' \
  -d '{"messages":[{"role":"user","content":[{"type":"text","text":"Hello"}]}]}'
```

The idempotency header is optional for invocation but strongly recommended for requests that may be
retried. Equivalent retries return the original stored response; changed input with the same key
returns `409`.

## Streaming Workflow

```bash
curl -N -X POST http://127.0.0.1:8000/v1/agents/support-native/invoke/stream \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: http-stream-1' \
  -d '{"messages":[{"role":"user","content":[{"type":"text","text":"Hello"}]}]}'
```

The stream uses Server-Sent Events. Successful streams start with `started`, may emit `text_delta`
or `tool_result`, and end with `completed`. After HTTP 200 headers are sent, failures use `event:
error` with the standard safe error envelope. Configure reverse proxies to disable buffering and
allow a timeout longer than the runtime invocation deadline.

## Error Reference

| HTTP status | Typical meaning | Caller action |
| --- | --- | --- |
| `400` | Other safe runtime request failure | Correct the request or application state |
| `404` | Unknown agent, thread, or tool | Refresh discovery or caller state |
| `409` | Idempotency conflict, unavailable replay, or active run | Follow idempotency policy |
| `413` | Request exceeds `MAX_REQUEST_BODY_BYTES` | Reduce the request body |
| `422` | Validation, guardrail, or tool-argument rejection | Correct input; do not blindly retry |
| `502` | Provider, framework, or tool dependency failed | Inspect protected dependency telemetry |
| `503` | Readiness dependency unavailable | Remove the instance from traffic |
| `504` | Invocation or tool timeout | Resolve latency before increasing limits |

Errors use a stable envelope containing `request_id`, `code`, safe `message`, and `retryable`.
Stack traces and provider/database details remain internal.

## Verification

```bash
uv run pytest tests/unit/test_service.py tests/integration/test_service_e2e.py -q
python scripts/docker_smoke.py
```

Verify body limiting, validation, discovery, liveness/readiness separation, idempotent replay,
feedback, SSE terminal behavior, and shutdown in the deployment environment.

## Troubleshooting

### `/healthz` succeeds and `/readyz` returns 503

Check database connectivity and compare `uv run alembic current` with `uv run alembic heads`. Keep
the instance out of traffic until both checks pass.

### Request returns 422

Inspect the generated OpenAPI contract and canonical Pydantic fields. Public validation errors are
intentionally generic; reproduce with sanitized input in a protected development environment.

### Request returns 409

Retry an active equivalent request with backoff. Use a new key for changed input. Do not discard a
successfully replayed terminal response.

### Stream buffers until completion

Use `curl -N`, disable proxy buffering, preserve `text/event-stream`, and avoid response-transforming
middleware. The service already sets `Cache-Control: no-cache` and `X-Accel-Buffering: no`.

### Client disconnects during a stream

Close the client iterator and resolve or retry using the same idempotency key. Confirm ASGI server
and proxy cancellation reaches the runtime.

## Security Notes

Bind the example to localhost. Before any public exposure, add authenticated identity,
authorization for agents/threads/feedback, tenant isolation, request and concurrency limits, TLS,
safe CORS policy, network controls, and abuse monitoring. Treat request IDs as correlation values,
not credentials.

## Related Documentation

- [User Runbook](../user-runbook.md)
- [Runtime](runtime.md)
- [Feedback](feedback.md)
- [Example Application](example-application.md)
