# Agent Runtime Kit

A framework-neutral Python runtime toolkit for building configurable, observable AI agents
with PostgreSQL-backed memory.

> **Project status:** early development. Framework-neutral contracts, native execution,
> LangChain/LangGraph adapters, and the first PostgreSQL memory slice are available.

## Why this project

Agent applications repeatedly need the same runtime facilities: stable invocation contracts,
tool validation, lifecycle policy, durable memory, idempotency, feedback capture, and telemetry.
This package keeps those concerns independent from any single agent framework.

## Design boundaries

- The core package does not import LangChain or LangGraph.
- PostgreSQL is the only persistence backend planned for the MVP.
- Async interfaces are the default.
- Prompt and tool content is not captured by telemetry by default.
- Feedback is append-only evidence, not an automatic learning loop.

## Development

Prerequisites are Python 3.11+, `uv`, and Docker for PostgreSQL integration tests.

```bash
uv sync --all-extras --dev
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
```

See [the architecture notes](docs/architecture.md) for component boundaries and extension points.

## PostgreSQL development

```bash
docker compose up -d postgres
uv run alembic upgrade head
```

The memory package is usable independently from the full runtime:

```python
from agent_runtime.memory import PostgresMemoryStore

store = PostgresMemoryStore.from_url(
    "postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime"
)
thread = await store.create_thread(namespace="example", user_key="synthetic-user")
await store.close()
```

## Non-goals

The MVP does not include an evaluation platform, workflow builder, hosted control plane, vector
search, background scheduler, or multiple persistence backends.

## Independent project disclaimer

This is an independent personal project built from public documentation and original
implementation. It is not affiliated with, endorsed by, or derived from any employer or client
codebase.

## License

MIT
