# HTTP Service Runbook

## Responsibility

The optional FastAPI package exposes discovery, invocation, SSE streaming, feedback, liveness, and
readiness while reusing the library's canonical Pydantic contracts.

## Endpoints

- `POST /v1/agents/{agent_id}/invoke`
- `POST /v1/agents/{agent_id}/invoke/stream`
- `POST /v1/feedback`
- `GET /v1/agents`, `/healthz`, and `/readyz`

The stream uses Server-Sent Events. Successful streams end with `event: completed`. Once HTTP 200
headers have been sent, runtime failures are represented by `event: error` containing the standard
safe error envelope.

## Verify

```bash
uv run pytest tests/unit/test_service.py tests/integration/test_service_e2e.py -q
python scripts/docker_smoke.py
```

## Common failures

- 413: reduce the body below `MAX_REQUEST_BODY_BYTES`.
- 422: validate against the generated OpenAPI contract.
- 409: retry an in-progress idempotent request or use a new key for different input.
- 502/504: inspect provider, tool, and timeout spans.
- Stream disconnect: close the client iterator and retry according to idempotency policy.

Authentication, authorization, rate limiting, TLS, CORS policy, and public ingress do not belong to
this optional demonstration service and must be supplied by the hosting application.
