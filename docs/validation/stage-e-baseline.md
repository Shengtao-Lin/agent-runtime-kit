# Stage E: Actual v0.6.0 Long-History Baseline

Measured on 2026-11-06. The frozen synthetic host requirements are **not met at 10,000 turns**.
Design, implementation and candidate acceptance remain outstanding; this is baseline evidence,
not completion of the full Stage E goal.

## Provenance and completion

[Requirements and safety ceilings](stage-e-workload.md) were committed at `d587d1f` before measurement.
The smoke-verified harness was committed at `d872906`. Its separate lock pins actual v0.6.0 and
SHA-256 `abe01feae67feab23f5a5625a3a19f99914bd24e10d87da711f14a119e027798`.

External non-editable venvs used Windows 11/Python 3.12.10, PostgreSQL 16.15, SQLAlchemy 2.1.8,
Psycopg 3.3.8 and Pydantic 2.14.3. Source hashes and complete dependencies are retained below.
Original Stage D helpers supply identical complete-turn seeding and synthetic review payloads;
the independent runtime installation uses actual v0.6.0.

All **42** fresh profile/lane/repetition processes completed: three repetitions of fourteen
combinations, **510** continuations (420 timing, 90 diagnostic). Four external pytest controls
passed. Every profile verified full row counts, exact replay without additional model/tool work,
zero checked-out connections, surviving tasks and tagged PostgreSQL sessions, and stopped RSS
sampling. No safety ceiling changed. Experiment completion is distinct from host-budget success.

- [Summary](evidence/stage-e-v060-baseline/summary.json): per-repetition budgets, source/dependency
  inventories and raw report digests.
- [Raw archive](evidence/stage-e-v060-baseline/raw.tar.gz): 42 reports, manifest, installed audit and
  pytest JUnit. SHA-256: `c3ceb26f4cdd8fd3331b35c94a9d13c258b01f8e0ef6e9891f2dae0ae8ec3a66`.
- [Checksums](evidence/stage-e-v060-baseline/checksums.json) for the summary and archive.

Local originals remain at `build/stage-e-v060-baseline-1/`; initial smoke is at
`build/stage-e-baseline-smoke-1/`. The [runner guide](../../validation/history_workload/README.md)
provides reproduction and comparison commands.

## Low-interference results

The timing lane has no tracemalloc, row/SQL timers or repeated full-history serialization. Latencies
pool three repetitions using nearest-rank p95. RSS is the worst per-process peak increase over its
own post-seed/GC baseline. Individual repetition results govern the absolute acceptance budgets.

| Initial turns | Concurrency | Invoke completion p95 | Stream first-text p95 | Maximum timing RSS increase |
| --- | --- | --- | --- | --- |
| 100 | 1 | 52.5 ms | 43.8 ms | 2.3 MiB |
| 1,000 | 1 | 187.9 ms | 202.3 ms | 26.6 MiB |
| 10,000 | 1 | 2,289.9 ms | 2,462.1 ms | 258.9 MiB |
| 1,000 | 4 | Outside this staged matrix | 659.1 ms | 72.2 MiB |

Every 10,000-turn timing process exceeded the 128 MiB RSS-increase requirement. All three stream
repetitions exceeded the 2-second first-text requirement; one invoke repetition exceeded 2 seconds.
Short and medium single-conversation profiles passed. Every initial input fit 65,536 bytes: Stage D's
entry guarantee works, while complete history materialization still grows.

## Diagnostic evidence

At 10,000 turns, diagnostic Python allocation peaks reached **205.1 MiB**, exceeding 128 MiB in every
invoke/stream repetition. Diagnostic RSS increases reached about **502 MiB**, including tracing/probe
overhead; these are not uninstrumented application-memory results.

One first-repetition invoke sample spent 4.094 seconds in history read, including 1.466 seconds in
canonical row conversion and 1.043 seconds in driver JSON decode. Cursor execution was 1.823 seconds;
these intervals overlap and must not be summed. Context selection took 1.723 seconds, model-input
measurement serialization about 6 ms, and total diagnostic completion 6.209 seconds. Diagnostic
timings are excluded from the 2-second responsiveness gate.

That process's separate server EXPLAIN reported 49.7 ms execution, 40,012 returned rows and temporary
sort blocks. It runs after measurement, not inside the same invocation, and cannot isolate network
time. The evidence supports investigating client materialization first; it does not establish an
exact additive cost breakdown or prove server work negligible for every deployment.

## Next decision and limits

Keep the frozen budgets. First evaluate batched cursor consumption within the existing store API,
preserving complete return values and historical validation. If retaining the full transcript still
prevents acceptance, evaluate optional incremental consumption/selection with legacy-store fallback.
Record alternatives in an ADR before promotion and retain failed candidates. Never skip old system
messages, malformed exchanges or incomplete groups to satisfy a budget.

Runs were sequential on a shared development host, not randomized or CPU-isolated. Database caches
were not cleared; isolated namespaces accumulate in the same disposable database and can change
plans. No concurrent large benchmark ran, but ordinary tooling and host activity were not excluded.
The repeated roughly twofold RSS-budget miss is stronger evidence than a marginal latency difference.

RSS sampling can miss short peaks; tracemalloc excludes native allocations. Content JSON text and
driver JSON input sizes are logical measures, not complete network/storage bytes. The short-profile
passes describe only this finite synthetic envelope, not production SLOs, universal bounds or provider
certification. Full Stage E still requires a justified fix, independent acceptance and delivery.
