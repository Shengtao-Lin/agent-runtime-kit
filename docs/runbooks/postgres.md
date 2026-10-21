# PostgreSQL Runbook

## Purpose and Ownership

PostgreSQL 16 is the sole durable backend for threads, messages, long-term memory, runs,
idempotent responses, and feedback. Applications use repository protocols rather than importing or
mutating ORM tables. Alembic owns schema evolution.

Use this runbook for connectivity, migrations, pool pressure, message sequencing, expiration
cleanup, backup expectations, and readiness. Runtime state-machine failures are covered in the
[Runtime Runbook](runtime.md).

## Prerequisites and Configuration

Install the `postgres` extra and provision a database user that can read and write runtime tables.
The migration job also needs schema-alteration privileges.

| Example setting | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime` | Async connection URL |
| `DATABASE_POOL_SIZE` | `5` | Persistent pool size per process |
| `DATABASE_MAX_OVERFLOW` | `5` | Additional bounded connections per process |
| `DATABASE_POOL_TIMEOUT_SECONDS` | `5` | Maximum pool acquisition wait |
| `AGENT_RUNTIME_TEST_DATABASE_URL` | unset | Disposable integration-test database |

Plan total connections as `(pool size + overflow) × process count`, plus migration and operational
connections. Do not solve pool timeouts by increasing limits before checking transaction duration
and leaks.

## Start, Migrate, and Verify

### Installed wheel (development builds for v0.2.0)

The wheel ships the complete Alembic script directory under `agent_runtime.migrations`. Install
the `postgres` extra, set an explicit database URL, and run from any working directory:

```powershell
$env:DATABASE_URL="postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime"
python -m agent_runtime.migrations upgrade
agent-runtime-migrate current
agent-runtime-migrate heads
```

On POSIX shells, set the same URL with `export DATABASE_URL=...`. `upgrade` defaults to `head` and
accepts an explicit revision; `upgrade --sql` generates SQL without connecting. `heads` needs no
URL and opens no database connection. Missing targets and failed operations return nonzero exit
codes. The CLI does not print raw database exceptions or credentials.

A synchronous Python migration job can also call:

```python
from agent_runtime.migrations import upgrade

upgrade(database_url)
```

Run this function outside an active asyncio event loop, preferably in a separate migration process.
It closes its non-pooled engine in a `finally` block and selects a Windows-compatible event loop.
URLs are passed directly rather than through ConfigParser, preserving percent-encoded credentials.

Revision `0001` remains unchanged. Development head `0002` adds fenced thread ownership.
Stop/drain all old workers before upgrading; mixed-version rollout is unsupported. The migration
preserves history, successful responses, feedback, and memory, but settles legacy accepted/running
rows as failed with `legacy_owner_unavailable` because they have no ownership proof. Back up and
inspect unfinished work first. No restamp or rebuild is needed; repeating `upgrade` is a no-op.

The published v0.1.1 wheel does not have this command. Use its matching release source archive or
checkout to run its migration job until v0.2.0 is published.

### Repository checkout (compatible existing commands)

The root `alembic.ini` now resolves the packaged script directory. Existing commands remain valid:

```bash
docker compose up -d postgres
uv run alembic upgrade head
docker compose ps postgres
uv run alembic current
uv run alembic heads
```

Expected result: the container is healthy and `current` equals `heads`. The optional HTTP service's
`/readyz` must remain unavailable until both connection and expected-revision checks succeed.

To run the PostgreSQL test suite in PowerShell:

```powershell
$env:AGENT_RUNTIME_TEST_DATABASE_URL="postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime"
uv run pytest -m integration -q
```

Use a disposable database. Integration tests may create, update, and remove their own records.

## Library Workflow

```python
from agent_runtime.memory import PostgresMemoryStore

store = PostgresMemoryStore.from_url(database_url)
thread = await store.create_thread(
    namespace="my-application",
    user_key="synthetic-user",
)
await store.put(
    namespace="preferences",
    user_key="synthetic-user",
    memory_key="language",
    value={"language": "en"},
    tags={"kind": "preference"},
)
record = await store.get(
    namespace="preferences",
    user_key="synthetic-user",
    memory_key="language",
)
await store.close()
```

Prefer passing one application-owned async engine to memory, run, and feedback stores. Close the
engine once during shutdown after in-flight work completes.

Conversation operations `get_thread`, `get_messages`, and `append_messages` accept an optional
`namespace=` argument. Scoped thread lookup returns `None` for missing/foreign IDs; scoped history
and append raise `ThreadNotFoundError`, including empty appends. `cleanup_expired(namespace=...)`
restricts maintenance to that namespace. Omitting the argument preserves trusted unscoped access,
not a security guarantee. Runtime calls always supply their configured namespace. No schema change
is needed; custom memory implementations must accept these keywords and apply equivalent checks.

## Operational Checks

Successful finalization uses `PostgresRunStore.complete()` to atomically append messages and store
the succeeded run plus canonical response. Mutations consistently lock the thread before the run.
`fail_if_running()` checks the original owner before deciding whether cleanup is needed, preserving
success when a commit outcome is uncertain. Standalone memory appends use the same ordinal
allocator within their own transaction. Do not append directly to an active runtime thread.
The development schema head and default HTTP readiness revision are `0002`.

- Monitor connection acquisition latency, active and idle connections, storage growth, transaction
  age, lock waits, query latency, and expired long-term records.
- Compare `alembic current` with the revision expected by the running application image.
- Verify thread message sequences are contiguous after concurrency tests.
- Verify completed idempotent responses remain replayable after process restart.
- Schedule `PostgresMemoryStore.cleanup_expired()` in an application-owned maintenance process; the
  library does not start a scheduler.
- Schedule bounded `PostgresRunStore.recover_expired(limit=100)` batches if abandoned runs need
  proactive recovery. This settles expired owners as failed; it never re-executes tools. Batches
  skip locked threads. Record recovered IDs/counts and categorical errors, not owner tokens.
- Test backups and restores against a non-production target before relying on them.

## Migration Procedure

1. Back up according to the deployment policy and identify the exact target database.
2. Review the migration and its compatibility with currently running application versions.
3. Stop or drain incompatible traffic when required.
4. Run `uv run alembic upgrade head` as an explicit one-shot job.
5. Confirm `current` equals `heads`, then check application readiness.
6. Run a synthetic create/invoke/replay/feedback flow.

Do not run schema migrations implicitly in each web process. Multiple application replicas should
not compete to alter the schema during startup.

## Troubleshooting and Recovery

### Connection refused or authentication failed

Verify host, port, network route, database name, user, password secret, and TLS requirements. Check
from the same execution environment as the app; `localhost` inside a container refers to that
container, not the Compose database service.

### Pool timeout

Inspect long transactions, leaked sessions, slow queries, and total replica connection demand.
Repair those conditions before adjusting pool limits. Keep acquisition bounded so readiness and
requests fail predictably under pressure.

### Migration revision is behind

Keep the application unready, run the explicit migration job against the same database, and verify
the revision. Do not disable the readiness check to force traffic through.

### Message sequence conflict

Retry the entire append transaction. Thread-row locking prevents normal concurrent writers from
reusing a sequence. Persistent conflicts indicate a caller bypassing the repository or an
unexpected transaction boundary.

### Invalid run transition

Preserve the row and investigate competing owners or repeated finalization. Do not rewrite status
manually; run state is evidence used for replay and diagnosis.

### Expired owner or permanently busy thread

Inspect `lease_expires_at` and the stored error. The database clock determines expiry. Runtime
leases include the invocation budget, cleanup budget, and five seconds of grace. An equivalent
runtime retry or a new thread claim triggers conditional recovery; operations can instead call
`await runs.recover(run_id)` or `await runs.recover_expired(limit=100)`. Live leases are not stolen.
Recovered keys return `idempotent_run_unavailable`. Reconcile uncertain external effects before
submitting a deliberate new request/key. See [ADR 0009](../adr/0009-fenced-thread-ownership.md).

### Expired records keep growing

Confirm an application-owned job calls `cleanup_expired()` and records its deleted count. Set the
job cadence from retention requirements and database load; the library intentionally provides no
background scheduler.

## Data and Security Notes

Backups, point-in-time recovery, restore drills, high availability, encryption, credentials,
network policy, retention, and tenant isolation are deployment obligations. A namespace is not a
database authorization boundary. Never run destructive recovery or cleanup commands without a
verified backup, an exact database target, and an approved retention policy.

## Related Documentation

- [User Runbook](../user-runbook.md)
- [Runtime](runtime.md)
- [Feedback](feedback.md)
- [Architecture Decision 0002](../adr/0002-postgresql-only-memory.md)
