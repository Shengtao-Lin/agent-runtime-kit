# Example Application Runbook

## Responsibility

The fictional support agent proves library integration. It is not the product boundary and is not
included in the Python wheel. It uses synthetic `DEMO-*` data only.

## Start and verify

```bash
docker compose build app
docker compose run --rm migrate
docker compose up -d --wait app postgres
python scripts/docker_smoke.py
docker compose down
```

For local Python execution:

```bash
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m examples.support_agent.main --fake-model
```

## Provider mode

Fake mode is deterministic and credential-free. For a real provider, set `FAKE_MODEL=false`, choose
`MODEL_PROVIDER=openai_compatible`, `anthropic`, or `gemini`, and supply provider configuration
through the environment.

## Common failures

- Migration container fails: inspect its logs and keep the application unready.
- App is healthy but not ready: check PostgreSQL and Alembic revision.
- Port collision: stop the conflicting local process or change the explicit Compose port mapping.
- Real provider fails while fake mode works: use the model-provider runbook.

The container runs as `agent-runtime`, closes the database engine, flushes telemetry, and uses a
bounded graceful shutdown interval.
