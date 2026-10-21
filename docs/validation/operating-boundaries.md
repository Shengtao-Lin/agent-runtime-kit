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
