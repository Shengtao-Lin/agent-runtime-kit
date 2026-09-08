# PostgreSQL Runbook

## Responsibility

PostgreSQL 16 is the sole persistence backend for threads, messages, long-term memory, durable runs,
idempotent responses, and feedback. Applications access it through repository protocols, not ORM
tables.

## Start, migrate, and verify

```bash
docker compose up -d postgres
uv run alembic upgrade head
uv run pytest -m integration -q
```

`/readyz` must remain unavailable until both a database connection and the expected Alembic
revision succeed.

## Operational checks

```bash
docker compose ps postgres
uv run alembic current
uv run alembic heads
```

Monitor pool acquisition latency, active connections, storage, transaction age, and expired
long-term records. Run `PostgresMemoryStore.cleanup_expired()` from an application-owned maintenance
process when required; the library does not start a scheduler.

## Common failures and recovery

- Connection refused: verify host, port, network, credentials, and TLS settings.
- Pool timeout: find leaked or slow transactions before increasing pool limits.
- Migration behind: stop traffic to incompatible code, run the explicit migration job, then check
  readiness again.
- Invalid run transition: preserve the row and investigate competing owners; do not rewrite status.
- Sequence conflict: retry the whole append transaction. Thread-row locking prevents normal
  concurrent writers from reusing a sequence.

Backups, restore drills, high availability, encryption, and retention are deployment obligations.
Never run destructive recovery commands without a verified backup and an exact database target.
