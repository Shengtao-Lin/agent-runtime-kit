# Independent Consumer Validation and v0.2.0 Readiness

## Baseline and Method

Validation date: 2026-10-11 (America/New_York).

The baseline is the publicly released v0.1.1 wheel, not an editable checkout. The application is in
[validation/consumer](../../validation/consumer/README.md), with a separate dependency manifest and
lockfile. [The runner](../../scripts/validate_consumer.py) copies it outside the checkout and creates
a fresh venv. It imports only installed library modules and uses an application-owned synthetic
model and typed tool.

The baseline also downloads the released source archive for an explicit Alembic migration job.
That archive's SHA-256 is checked before copying its four known migration files. Neither migration
nor application execution imports the checkout's library source.

Local execution used Windows, Python 3.12.10, PostgreSQL 16, pytest 8.4.2, Pydantic 2.14.1,
SQLAlchemy 2.1.4, Psycopg 3.3.6, and OpenTelemetry 1.45.1. These downstream dependencies intentionally
differ from the repository's development lock; the consumer lock records the exact baseline.

## Measured Result

The published baseline produced **8 passed, 4 xfailed**, with no ordinary failures. The four expected
failures are executable reliability targets, not successful integration checks. Each failure type
is restricted so dependency or subprocess failures are not silently accepted as a known gap.

| Area | Observation | Result |
| --- | --- | --- |
| Package installation | Actual release wheel installed inside the consumer venv; no editable source, example application, FastAPI, or framework packages | Passed |
| Schema setup | Source-archive migration applied to a dedicated database; connection and revision `0001` ready | Passed with separate migration artifact |
| History and replay | New application instance replayed the original stored JSON without a model call; next turn saw three messages and persisted four contiguous messages | Passed |
| Tools and streaming | Synthetic double tool returned `14`; stream emitted a tool result, two text deltas, and one durable completion | Passed |
| Streaming replay | Replayed `started` and `completed` without repeating model/tool work | Passed |
| Feedback | Equivalent retry returned the original record; correction appended a new record; two pages used `next_offset` | Passed |
| Memory | Namespaced lookup, filtered search, and explicit deletion worked independently of invocation | Passed |
| Failure handling | Guardrail blocked before model execution; timeout and closed stream persisted terminal failure | Passed |
| Idempotent concurrency | A retry while its owner was running was rejected, then replayed after completion; one model call | Passed |
| Resource lifecycle | Application closed its model, disposed its shared engine, and shut down telemetry | Passed |

The runner writes JUnit results, package provenance, and dependency inventory under
`build/consumer-validation/`. CI uploads baseline and candidate reports as
`independent-consumer-results`.

## Reproduced Gaps

| ID | Trigger and evidence | Impact | v0.2.0 target |
| --- | --- | --- | --- |
| R1 | Kill the child process after its model signals that the durable run is running; a new application instance still reads `running` | Equivalent retries cannot progress without operator intervention | Explicit owner lease, fencing, and stale-run recovery policy |
| R2 | Two different requests continue the same thread while the first model call is held open; both see three messages rather than serial histories of three then five | Responses may omit the concurrent turn even though message ordinals remain unique | Serialize same-thread invocations or explicitly reject overlaps |
| R3 | Inject failure only in the successful run transition after the model completes; the run becomes failed but its user/assistant messages remain committed | History and replay disagree about whether a turn succeeded | Commit message batch and terminal canonical response in one persistence transaction |
| R4 | A before-model hook sleeps 100 ms with a 20 ms invocation timeout; invocation still succeeds | The documented total deadline is not enforced around policy or persistence | Define and enforce a deadline across all phases, with bounded finalization behavior |
| R5 | Inspect installed wheel resources: migration files are absent; migrations require the separate source archive | A wheel-only deployment cannot initialize its database without another artifact | Ship migration resources and provide a supported explicit migration entry point |

R1-R4 are strict expected failures in `tests/test_readiness.py`. R5 is a packaging observation
recorded by the package audit and demonstrated by the source-archive migration requirement.

## Proposed v0.2.0 Delivery Sequence

### Consolidated candidate status

The five measured gaps now have candidate acceptance coverage rather than candidate expected
failures. The published baseline remains unchanged. Current local validation uses the project
venv and PostgreSQL: 153 root pytest cases pass after initial-spec closure; independent candidate
installation passes 18 cases with one upgrade-only skip; the v0.1.1 upgrade passes 19.
Candidate runs deselect
the four frozen historical probes. Release-candidate version metadata is `0.2.0`, schema `0002`;
this does not mean the version has been published. CI and the non-publishing Release workflow
provide the exact-commit evidence required before publication.

### Ownership and recovery implementation status (2026-10-20)

Development builds address R2 by rejecting overlapping turns on one thread across processes.
R1 is addressed by finite database-clock leases, owner-fenced mutations, and conditional recovery
to terminal failure, not automatic re-execution. Recovery runs on retries/new claims or explicit
application-owned maintenance. Revision `0002` requires drained old workers and preserves terminal
evidence while settling unowned legacy active runs. Candidate tests exercise independent pools,
real killed processes, stale owners, fresh histories, and upgrades. The frozen published baseline
keeps its four measured expected failures. See [ADR 0009](../adr/0009-fenced-thread-ownership.md).

### Full deadline implementation status (2026-10-20)

Development builds address R4 with one cooperative budget for all invocation phases, including
hooks and persistence. Separate bounded cleanup preserves the original error or reconciles
committed success. Candidate-wheel tests cover before/after-model hooks in invoke and stream;
the published baseline retains its R4 expected failure. See
[ADR 0008](../adr/0008-full-invocation-deadline.md) for cancellation and wall-clock limits.

### Atomic finalization implementation status (2026-10-20)

Development builds address R3 through `RunStore.complete()` and conditional `fail_if_running()`
cleanup. PostgreSQL commits the message batch and successful canonical response together.
Commit-acknowledgment reconciliation preserves durable success; rollback and cancellation cannot
leave a partial message batch. A cleanup outage preserves the original error and leaves durable
state for inspection/recovery.

Database fault tests exercise invoke/stream, response ownership, terminal immutability, and cleanup
failures. Candidate-wheel tests verify rollback at the SQL-update boundary and replay after a lost
acknowledgment. The frozen baseline still reproduces the v0.1.1 R3 failure. See
[ADR 0007](../adr/0007-atomic-run-finalization.md) for custom-store migration requirements and limits.
No schema migration is needed for this change.

### Migration packaging implementation status (2026-10-12)

R5 is implemented in development builds: migrations ship under `agent_runtime.migrations`, with
`python -m agent_runtime.migrations` and `agent-runtime-migrate` entry points. Revision `0001` is
unchanged. Repository and Docker jobs resolve the same packaged resources.

The candidate validation path no longer copies migration code from the checkout. It initializes
a database through the installed wheel and repeats the upgrade. A separate upgrade check seeds
messages, a canonical response, feedback, and long-term memory with the released v0.1.1 wheel,
then verifies the candidate migration preserves them. The baseline remains pinned to v0.1.1 and
still records R1-R4 as expected failures. See the later publication evidence below.

### 1. Make wheel-only setup complete

Package versioned migration resources and expose a documented entry point that does not depend on
a checkout or import ORM tables into consumer code. Keep migrations explicit rather than running
them in every application worker.

Acceptance: an empty database becomes ready using only the installed wheel and its optional
dependencies; upgrading an existing v0.1.1 database preserves history, replay, and feedback.

### 2. Make finalization atomic

Define a shared persistence operation for committing the generated message batch and successful
run response. Keep the generic runtime independent of SQLAlchemy implementation details.

Acceptance: faults before commit leave neither successful messages nor a successful response;
faults after a committed response remain replayable. Tool side effects remain an application
concern and must not be falsely described as transactionally rolled back.

### 3. Define the complete deadline

Apply the budget to claim/history, hooks, agent/provider/tools, and finalization. Define behavior
when the budget expires while the database is unavailable, including a bounded cleanup attempt and
how recovery resolves unfinished durable state.

Acceptance: slow hooks and persistence paths cannot escape the documented request bound; external
cancellation retains its categorical error and closes streaming producers.

### 4. Define same-thread concurrency

Choose a documented serialize-or-reject policy. Enforce it across multiple processes rather than
only with an in-process lock. Keep thread policy distinct from per-agent idempotency arbitration.

Acceptance: accepted turns consume a consistent history, or overlaps return an explicit retryable
conflict. Existing single-request replay does not invoke tools again.

### 5. Add crash recovery with ownership fencing

Introduce a durable lease and ownership token before allowing stale-run recovery. A stale worker
must not commit after ownership has changed. Specify what happens to failed keys and uncertain tool
side effects; do not automatically rerun external writes merely because a worker disappeared.

Acceptance: killing a worker leads to bounded, observable recovery; equivalent retries converge on
one result or an explicit terminal failure. Recovery behavior is exercised by updating the current
crash probe to wait for or invoke the supported policy.

## Validation Limits

### Independent framework extension

The optional `--frameworks` runner path adds application-owned real Runnable and StateGraph
consumers with custom state, shared typed tools and public message mappers. Neither imports the
repository example. It verifies tool/history equivalence, restart replay, feedback, safe errors,
and cancellation through host shutdown. The fresh installed-wheel result is 24 passed, one
upgrade-only skip and four historical probes deselected. See
[the framework consumer](../../validation/frameworks/README.md) for locked versions and execution.
The default frozen baseline still installs no framework dependencies.

### Remaining limits

This baseline verifies the Python library with `postgres` and `telemetry`, not live provider
accounts, framework versions, HTTP ingress, performance, or a multi-replica production deployment.
The synthetic model makes contracts deterministic but does not assess model quality. Telemetry
shutdown is exercised; remote OTLP delivery and collector outage behavior remain covered by other
tests. Application identity/authorization and tenant isolation remain application responsibilities.

The proposed work is a readiness plan. The v0.1.1 runtime behavior has not been changed by this
validation project. v0.2.0 subsequently passed publication acceptance; see
[the immutable release evidence](v0.2.0-publication.md). Main now prepares v0.3.0.
