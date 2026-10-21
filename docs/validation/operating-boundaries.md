# Compatibility and Operating Boundaries

## Dependency evidence

PR CI verifies locked dependencies on Python 3.11/3.12 (Linux) and 3.12 (Windows).
The independent consumer optionally resolves a clean complete dependency set after wheel install:

- `locked`: the consumer lock, with optional pinned framework group.
- `lower`: lowest mutually resolvable direct dependencies, with LangChain Core 0.3.x and LangGraph
  0.2.28..0.2.x for the historical v0.2.0 wheel. For the v0.3.0 candidate the graph lower bound is
  1.2.11, with the lowest mutually compatible core version. This is one compatible combination,
  not every possible
  combination or the claim that LangGraph 0.2.0 supports LangChain Core 0.3.
- `latest`: highest mutually resolvable versions allowed by wheel metadata.

Each profile uses a complete compiled requirements file and `uv pip sync`, so obsolete dependencies
cannot survive a downgrade. `uv pip check` runs before migration/tests. Exact inventories and
resolved requirements accompany JUnit and package provenance. Latest is a time-dependent probe;
the saved resolution is the reproducibility artifact. The published baseline lock stays unchanged.

The compatibility workflow runs lower/latest on Python 3.11/3.12 for workflow changes, manually and
weekly. Normal PRs keep the smaller locked matrix. A failed compatibility probe requires investigation
or narrowing the published range, not silently skipping failures.

Local Python 3.12 acceptance: both lower and latest passed 27 tests, with one upgrade-only skip and
four frozen historical probes deselected. Lower resolved Pydantic 2.9.0, SQLAlchemy 2.0.0,
Psycopg 3.2.1 and OpenTelemetry 1.27.0. SQLAlchemy 2.0.0 emits a Python datetime deprecation warning.
These results do not establish support for untested Python majors or every dependency permutation.

The v0.3.0 incremental probe on historical graph 0.2.28 exposed an internal queue waiter surviving
cancellation despite passing ordinary assertions. The adapter extra was narrowed to >=1.2.11,
and consumer runs treat pytest unraisable-exception warnings as errors. Historical non-streaming
v0.2.0 evidence remains separate from v0.3.0 support claims.

## Database fault evidence

The installed-wheel candidate uses real PostgreSQL, not mocked SQLAlchemy:

- Exhaust every application pool slot: the invocation deadline expires before model work or a
  durable claim; after releasing slots the same key succeeds.
- Hold a thread row lock from an independent pool: timeout cancels the blocked transaction,
  returns the application connection, leaves no claim/history, and the key works after unlock.
- Terminate only the application's idle backend: configured pool pre-ping replaces it and the
  next invocation succeeds. This does not imply in-flight transactions transparently retry.

Together with the atomic-finalization, killed-worker/fencing and real SIGTERM tests, these cover
specific failure boundaries. They do not certify arbitrary network partitions or a production
multi-replica deployment. No test restarts a shared database or deletes its volume.

## Synthetic performance baseline

Add `--benchmark` to candidate consumer validation to write `benchmark.json` alongside the exact
package/dependency inventory. Default: 20 samples per scenario, concurrency four, shared pool
size two plus two overflow connections, warmup before measurement, no remote collector/provider.
Set `CONSUMER_BENCHMARK_SAMPLES` (1..1000) and `CONSUMER_BENCHMARK_CONCURRENCY` (1..32) explicitly
for comparisons. Use a disposable database; generated synthetic rows are retained for inspection.

Scenarios: fresh invoke, typed tool invoke, native text stream, slow native consumer, durable replay.
The slow consumer receives 128 eight-character deltas with a requested 20-millisecond async delay per delta,
exceeding the runtime's 64-event queue capacity. It asserts complete text delivery and zero checked-out
database connections after the scenario. Reports contain p50,
p95, requests/second, peak traced Python allocations and model call counts; streams add first-text
delta latency. Replay asserts zero model calls. These timings include PostgreSQL and Python tracing
overhead, exclude caller queue time before the concurrency gate and do not measure process RSS.
First text delta differs from the initial `started` event. The slow-consumer peak is a bounded
synthetic baseline, not sustained-load memory growth or a framework-internal queue certification.
The requested delay exceeds common Windows timer resolution; actual timer precision remains
platform dependent. Reports include monotonic clock resolution rather than treating sleep as a
minimum wall-clock guarantee.
No regression threshold is enforced until repeated
measurements on a stable runner establish normal variation.

## Sustained-operation probe (v0.4 development)

`scripts/validate_consumer.py --wheel PATH --expected-version 0.4.0 --acceptance-only --frameworks
--soak-seconds 120` runs an additional native synthetic workload from the external installed-wheel
venv. Select an empty disposable PostgreSQL database through `AGENT_RUNTIME_TEST_DATABASE_URL`.
Duration is 5..1800 seconds (zero disables); `CONSUMER_SOAK_CONCURRENCY` is 1..16, default four.
CI/Release and clean lower/latest profiles use 30 seconds; longer rehearsals remain explicit.
Add `--soak-shared-engine` to reuse one application-owned engine across runtime lifetimes instead
of constructing/disposing it each cycle. Compatibility CI uses this shared mode; normal CI/Release
use per-cycle mode. The disposable database must have no unrelated application clients.

Every cycle creates a host lifetime and bounded database pool, executes concurrent independent
multi-turn calls, tools, a 128-delta slow consumer beyond queue capacity and exact successful replay,
then cancels an entered invocation and abandons a backpressured stream. It requires failed runs
with no partial messages/responses, checked-out connections returning to zero and no surviving
async tasks after checked shutdown/resource disposal. The model call count proves replay adds no
generation. Cycles use unique application identities and retain synthetic database evidence.
After full teardown a separate observer checks PostgreSQL for remaining application sessions,
excluding its own connection. Per-cycle mode also retains bounded engine weak-reference probes.

`soak.json` records the installed version, duration/concurrency/scenario counts, bounded allocation
checkpoints, thread counts and task/pool assertions. The fixture deliberately clears its call
history after assertions so that an intentionally recording fake model is not mistaken for a
runtime leak. Report bookkeeping is capped at 32 checkpoints; Python allocations are sampled after
garbage collection and are informational, not RSS or an enforced memory regression threshold.
Named threads and top allocation-location deltas after warmup help investigate growth rather than
mistaking executor initialization, compiled-type caches or measurement overhead for a proven leak.
Repeated SQL/driver cache warmup and measurement overhead remain part of the recorded process.

This is a short synthetic stability gate, not a production throughput SLA, a multi-hour soak, a
network-partition test or certification of framework-internal/provider buffers. Hard process kills
and non-cooperative extensions still require the separate ownership/shutdown recovery procedures.
