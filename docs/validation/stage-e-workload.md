# Stage E: Long-History Read and Resource Workload

Status: specification frozen before Stage E baseline measurement on 2026-11-06.
This is an engineering acceptance workload, not a production capacity or SLO claim.

## Host requirement and immutable baseline

Release Desk needs the current release review while retaining its complete audit history. Extend
the same deterministic offline scenario to 100, 1,000 and 10,000 historical turns, with one user,
assistant call, tool result and assistant answer per turn. Keep 1 KiB synthetic tool padding and
the explicit 65,536-byte initial window. Seed complete canonical turns in bounded batches outside
measurement, then execute real runtime continuations; do not claim all seed rows were generated
by separate runtime invocations. Historical system and unusual tool cases belong in correctness
acceptance, not silently changed performance fixtures.

The baseline is actual v0.6.0, wheel SHA-256
`abe01feae67feab23f5a5625a3a19f99914bd24e10d87da711f14a119e027798`, installed non-editably in an
external venv. Freeze a separate validation lock and record source hashes, installed dependencies,
Python/platform/PostgreSQL versions and asset provenance. Preserve Stage D's lock and raw evidence.

The following **synthetic host requirements** are chosen before measurement to decide whether an
implementation change is justified. They allocate a practical local interactive budget and room
for host-owned code; they are not measured production limits:

| Requirement | Acceptance on the fixed local environment |
| --- | --- |
| Initial canonical context | At most 65,536 bytes, with the v0.6.0 selection/validation semantics |
| Single-conversation responsiveness | Low-interference invoke completion p95 and stream first-text p95 each at most 2 seconds through 10,000 initial turns |
| History-related working set | Measurement RSS increase over post-seed/GC RSS at most 128 MiB per single-conversation process; report absolute RSS too |
| Python allocation diagnostic | Measurement tracemalloc peak at most 128 MiB for single-conversation profiles; diagnostic timing is not used for responsiveness acceptance |
| Short-history regression | Candidate timing p95 at 100 turns may increase by at most the larger of 20% or 20 ms, compared with the matched baseline |
| Resource correctness | Exact replay performs no new model/tool work; zero surviving tasks, checked-out connections or tagged PostgreSQL sessions after teardown |

Require each of three independent repetitions to satisfy absolute host budgets. Use the aggregate
matched comparison for the short-history regression check, retaining repetition results. A budget
miss is evidence, not permission to relax the target. RSS sampling can miss brief peaks and Python
allocation counts exclude native/driver memory; neither proves a universal hard bound.

## Staged matrix and experiment safety

Full profiles: 100/1,000/10,000 turns at concurrency one in invoke and stream modes, plus 1,000 turns
at concurrency four in stream mode. Never combine 10,000 turns and high concurrency or large payloads
in this stage. Each profile/lane/repetition runs in a fresh child process and isolated namespace.
Use three repetitions, twenty measured continuations per timing profile (five per thread at c4),
and three per thread per diagnostic profile. Smoke uses 100-turn invoke and ten-turn/c4 stream,
two continuations per thread, and both lanes. Smoke cannot satisfy full comparison acceptance.

Safety ceilings, distinct from the host acceptance budgets:

- At most 40,000 seeded messages per thread, 160,000 per profile; assert before seeding.
- At most 64 MiB stored content JSON text per thread after seeding; check in PostgreSQL without
  transferring message bodies. Individual fixture messages are below 2 MiB.
- A 1 GiB sampled process RSS abort guard runs every 10 ms; sampler failure fails the experiment.
- A 270-second cooperative child deadline and 300-second parent subprocess deadline cover seeding,
  measurement, diagnostic probes and cleanup. Invocation/queue budgets remain 30 seconds.
- Host admission four; database pool two, no overflow, five-second pool timeout; runtime shutdown
  budget six seconds. No model credentials, external tool effects or paid endpoints.

Do not erase failed reports or overwrite manifests. Abort/timeout evidence is not a passing sample.
Keep disposable data in the Stage E Compose volume; stop only that project when finished.

## Measurement boundaries

**Timing lane:** no tracemalloc, per-message serialization counters, per-row Python timers or SQL
event instrumentation. Only monotonic invocation/queue/first-text timers, constant-size runtime
context counters, sample bookkeeping and the RSS safety sampler run during measurement. Verify
full transcript counts, exact replay and cleanup outside the measured section. Do not measure a
whole-history JSON serialization merely to prove it was not sent to the invoker.

**Diagnostic lane:** record total history-read and context-selection wall time, SQL cursor execution
wall time, driver JSON decode and canonical row-conversion time, counts and logical payload sizes,
plus tracemalloc and RSS. Mark nested/overlapping intervals; do not add them as independent costs.
Instrumentation must wrap the installed implementation rather than substitute a different read path.
Measure diagnostic overhead by comparing matched timing/diagnostic samples, not by subtracting a
guessed constant from production latency.

PostgreSQL `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` on the same history query runs separately after
measurement. It records server execution/scan behavior, not transport or client decode. Cursor wall
time includes transport, driver work and scheduling; its residual is not an independently measured
network time. JSON decode counters cover driver JSON materialization, and row-conversion counters
cover the store's canonical conversion. Record what cannot be isolated instead of inventing numbers.
Logical JSON text bytes and stored bytes are not network-wire bytes. Keep query plans free of
message bodies and identifiers in retained public evidence.

## Decision and delivery

After baseline measurement, compare no change, host conversation rotation, streamed/page scanning,
selective reads and required metadata/index changes. Do not promise bounded database scanning merely
because Python materialization improves. Full historical malformed-exchange validation, global system
retention and complete cross-boundary tool groups must preserve their documented behavior.

Only a reproduced host-budget miss justifies a library optimization. Choose the smallest change
that meets all frozen acceptance requirements; adding a public store method is not predetermined.
If no change is justified, repeat the baseline as the no-change candidate and publish the supported
finite envelope and reevaluation triggers. Either outcome retains three matched repetitions,
correctness/compatibility/cleanup evidence, CI smoke, updated ADR/runbooks and final-head/main gates.
Library changes additionally require actual publication and independently locked Release Desk adoption.
