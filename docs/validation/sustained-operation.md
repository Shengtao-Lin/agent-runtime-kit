# Installed-Wheel Sustained Operation

## Purpose and limits

These synthetic native-model/PostgreSQL probes exercise repeated host lifetimes from a separately
installed wheel, not the source checkout. They supplement short timings and individual fault tests;
they are not a production SLA, live-provider certification, process-RSS bound or multi-hour soak.
See [operating boundaries](operating-boundaries.md) for commands, parameters and measurement scope.

## Workload and deterministic checks

Each concurrency-four cycle performs two conversation turns, one tool loop, a 128-delta slow stream,
successful replay with no extra model call, cancellation after model entry and stream abandonment
under backpressure. It then exits the checked runtime lifetime and closes host-owned resources.
Failed/cancelled work must not have a successful response or partial persisted messages.
Async tasks and checked-out connections return to zero. A final observer checks PostgreSQL for
remaining application sessions in the dedicated database; it excludes only its own connection.

Memory reporting records post-GC Python allocations, bounded checkpoints, named threads and top
allocation-location changes after warmup. Recording-model history is cleared only after call-count
assertions. No arbitrary memory threshold is imposed. Per-cycle engine weak-reference probes and
a shared-engine control distinguish runtime retention from repeated pool/dialect initialization.

## Local v0.4-development evidence

Windows Python 3.12, PostgreSQL 16, installed v0.4 candidate, synthetic data only:

| Probe | Cycles | Outcome |
| --- | --- | --- |
| 120-second engine-per-cycle workload | 334 | Task/pool and durable-state assertions passed |
| 60-second diagnostic engine churn | 164 | Sampled engines collected; executor threads reached 20 plus main; allocations grew and later fell |
| 120-second shared host engine | 411 | Task/pool assertions passed; Python current allocations about 2.11 to 2.14 MiB; four threads throughout |
| Final 30-second shared mode with PostgreSQL observer | 102 | All assertions passed; zero remaining application sessions |
| Final 30-second per-cycle mode with PostgreSQL observer | 79 | All assertions passed; zero remaining application sessions |

Each final mode also ran normal installed-wheel/framework pytest acceptance: 35 passed, one
upgrade-only skip, four historical probes deselected. Reports are under `build/v040-soak-*`.
Scenario counts and inventories are retained with each report; timings/cycle totals vary by host.

## Integration finding

Per-cycle engine churn showed much higher type/compiler allocations than the shared-engine
control. Allocation traces point into SQLAlchemy-related paths, not proof of a particular upstream
defect or proof of unlimited growth. Sampled closed engines were collectible; the named executor
threads reached the default pool's bounded maximum on the 16-CPU host. The shared-engine control
was substantially flatter in this tested window. No library-internal cache was cleared or private
dependency state modified to manufacture a flat result.

Applications should create one bounded engine per host lifetime, not one per request. Repeated
runtime/host teardown remains tested separately. The short runs do not establish long-term memory
stability for every dependency profile, third-party framework, tool or remote provider.

CI and Release repeat the 30-second per-cycle gate; lower/latest compatibility CI repeats shared
mode. Both retain their environment inventory and reports. Failures require investigation, not
silently relaxing assertions, removing the failed mode or treating displayed partial text as success.
