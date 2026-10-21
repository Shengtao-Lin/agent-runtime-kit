# Independent Consumer Application

This is a downstream validation project with its own locked environment. It consumes the released
v0.1.1 wheel with only the `postgres` and `telemetry` extras. Its model and tool belong to the
application and use synthetic data; no provider key is needed.

Run it through [the isolation runner](../../scripts/validate_consumer.py). The runner copies this
project to a temporary directory outside the checkout, creates its own `.venv`, removes inherited
`PYTHONPATH`/`PYTHONHOME`, and executes pytest there. A provenance test rejects editable installs or
library imports outside that venv. The example agent, FastAPI, and framework dependencies must be
absent.

## Published Baseline

Start the repository's local PostgreSQL service, then create a dedicated disposable database once:

```bash
docker compose up -d --wait postgres
docker compose exec -T postgres createdb -U agent_runtime agent_runtime_consumer
```

In PowerShell:

```powershell
$env:AGENT_RUNTIME_TEST_DATABASE_URL="postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime_consumer"
.\.venv\Scripts\python.exe scripts/validate_consumer.py
```

On Linux/macOS:

```bash
export AGENT_RUNTIME_TEST_DATABASE_URL=postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime_consumer
uv run python scripts/validate_consumer.py
```

The wheel URL and hash are locked in this project's `uv.lock`. Baseline migrations are downloaded
from the v0.1.1 release source archive and checked against its SHA-256 digest. Only four known regular
files are copied to a separate temporary migration job. The baseline does not use the checkout's
`src`, examples, or migration implementation. The missing migration resources in the wheel are a
documented v0.2.0 packaging target.

The runner requires a configured database and fails on connection or migration errors. It does not
silently skip database tests. Records use unique agent IDs and namespaces; abrupt-exit probes leave
their synthetic run evidence in the disposable database. It does not drop or reset the database.

## Candidate Wheel

```powershell
uv build
.\.venv\Scripts\python.exe scripts/validate_consumer.py --wheel dist/agent_runtime_kit-0.2.0-py3-none-any.whl --expected-version 0.2.0 --acceptance-only --report-dir build/consumer-candidate
```

Replace the wheel filename and expected version when validating a future release. Candidate mode
installs that wheel over the locked baseline without changing dependencies, checks compatibility
with `uv pip check`, and runs `python -m agent_runtime.migrations upgrade` from an empty temporary
directory using only the wheel's packaged migration resources. It repeats the upgrade and reads
the current revision to verify idempotency. Update
the consumer lock deliberately if a candidate changes dependency requirements. Supply
`--expected-revision` to override the current defaults (baseline `0001`, candidate `0002`).

To validate preservation of v0.1.1 data during an upgrade, add `--upgrade-from-baseline`. This mode
migrates with the published source archive, seeds messages, an idempotent response, feedback, and
long-term memory using the released wheel, then installs the candidate wheel and runs its packaged
migration. The additional pytest check verifies all seeded data and replay behavior survive.
It also seeds a legacy running row and verifies migration settles it as
`legacy_owner_unavailable`, without changing the successful evidence.

## Acceptance and Readiness Results

The eight acceptance tests cover:

- installed-wheel provenance and minimal optional dependencies;
- explicit migration/readiness checks;
- invocation, history continuation, replay after application restart, and changed-input rejection;
- native tools, incremental streaming, terminal persistence, and stream replay;
- immutable feedback, corrections, and offset pagination;
- standalone namespaced memory and deletion;
- guardrails, invocation timeout, stream cancellation, and durable terminal failure;
- one owner for an in-progress idempotent request; and
- model/engine/telemetry lifecycle ownership in the application.

Four separately marked readiness probes describe v0.2.0 targets: deadlines including hooks,
same-thread history consistency, atomic message/response finalization, and crash recovery. They use
strict `xfail` markers scoped to the expected failure type. Database and subprocess infrastructure
errors must fail the suite. An unexpected pass requires removing or redesigning the marker; these
gaps are not counted as successful acceptance tests.

The crash probe measures the persisted state immediately after the owner is killed. It demonstrates
that v0.1.1 has no recovery mechanism, not that a future lease must expire immediately. Update this
probe to invoke and wait for the chosen recovery policy when that API exists.

## Reports and CI

Reports are written to `build/consumer-validation/` by default:

- `results.xml`: pytest JUnit acceptance and expected-failure results;
- `installed-packages.json`: exact dependency versions; and
- `package-audit.json`: installed wheel provenance, platform, migration-resource inventory, and
  migration source.

CI runs the published baseline with gap probes, the built wheel against a fresh database, and
a v0.1.1-to-candidate data-preservation check. It uploads all three report directories. The source
project's normal pytest suite remains separate. Candidate tests also verify wheel migration files,
the console entry point, and rejection of a missing database target.

Candidate checks additionally inject a terminal SQL-write failure to verify that no message batch
is committed on rollback, and simulate a lost commit acknowledgment to verify replay without
repeating model work. These checks do not reinterpret the published v0.1.1 R3 expected failure.
Candidate checks also enforce hook deadlines, reject same-thread overlaps between independent
applications, require fresh continuation history, and kill a separate lease-owning process to
verify recovery without automatic model/tool work. The original readiness probes remain frozen.

See the [integration report and v0.2.0 plan](../../docs/validation/independent-consumer.md) for the
measured results, limitations, and implementation priorities.
