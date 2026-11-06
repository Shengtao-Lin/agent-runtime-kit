# Stage D: Published v0.5.0 Workload Baseline

Measured on 2026-11-05 (America/New_York), using the
[workload specification](stage-d-workload.md). This is completed baseline evidence, not completion
of Stage D or a production capacity claim. No library behavior, version or database schema changed.

## Provenance and reproducibility

The independent external venv installed the actual published v0.5.0 wheel, SHA-256
`d112163a318aa280e64bd20a0d8dd1db519007db3fc5aa419f0f3d1bdb60cd59`.
The workload has its own frozen lock and copied Release Desk business modules. Python 3.12.10 ran
on Windows 11 with a real PostgreSQL 16 Compose service, a dedicated `ark_stage_d_workload` database,
pool size two/no overflow and admission concurrency four. The manifest records workload and
application source hashes; the package audit records every installed dependency.

Checked-in [summary and provenance](evidence/stage-d-v050-baseline/summary.json) and adjacent raw
profile JSON preserve all 45 processes (15 profiles, three repetitions), totaling 972 measured
continuations. Each profile ran in a fresh process. The summary records raw-file SHA-256 digests.
Evidence JSON preserves its original bytes through Git attributes so those digests survive checkout.
The source hashes describe the measured local files, including their original line endings;
a later Git checkout may normalize line endings without changing the algorithm.

Four independent pytest controls passed. A separate candidate-install-path smoke using the same
published wheel also passed all four controls and two profiles; it validates installation plumbing,
not a changed candidate or a baseline/candidate performance comparison. The first smoke attempt
failed two PostgreSQL tests because Windows pytest used Proactor instead of Selector; the fixture
now explicitly supplies the supported event-loop policy. That failed local report is retained under
`build/stage-d-smoke-1`; it is not counted as a successful measurement.

## Observed results

Ranges below combine the three repetitions. History is the initial seeded turn count; each measured
success adds four messages. Every one-conversation profile has 20 measured continuations per repeat,
four-conversation profiles have five each, and sixteen-conversation profiles have two each.

| Initial turns / conversations / padding / mode | Initial invoker bytes (min-max) | Over 64 KiB / samples | End-to-end p95 ms | History read p95 ms | Measured RSS peak MiB (min-max) |
| --- | --- | --- | --- | --- | --- |
| 10 / 1 / 1 KiB / invoke | 27,369-79,068 | 15/60 | 59.8 | 11.7 | 77.3-77.8 |
| 100 / 1 / 1 KiB / invoke | 271,899-323,598 | 60/60 | 170.3 | 63.3 | 82.4-82.8 |
| 1,000 / 1 / 1 KiB / invoke | 2,717,199-2,768,898 | 60/60 | 1,072.2 | 436.7 | 129.4-130.1 |
| 1,000 / 1 / 1 KiB / stream | 2,717,199-2,768,898 | 60/60 | 1,064.0 | 398.7 | 129.2-129.8 |
| 10 / 4 / 1 KiB / invoke | 27,369-38,253 | 0/60 | 139.0 | 40.3 | 78.0-78.3 |
| 10 / 16 / 1 KiB / invoke | 27,369-30,090 | 0/96 | 540.1 | 35.6 | 77.6-78.0 |
| 10 / 1 / 16 KiB / invoke | 180,969-524,508 | 60/60 | 160.0 | 16.1 | 78.9-79.9 |
| 10 / 1 / 256 KiB / invoke | 2,638,569-7,651,548 | 60/60 | 249.2 | 56.6 | 97.0-97.3 |
| 10 / 4 / 1 KiB / slow stream | 27,369-38,253 | 0/60 | 294.1 | 32.1 | 77.6-77.7 |

The machine-readable summary includes every profile, first-text timing, admission wait, service
time, allocation peaks and measurement overhead. Percentiles pool repetitions and are descriptive.
At 1,000 turns, instrumentation serialization alone has a p95 around 596 ms for invoke; do not present
the total latency as uninstrumented runtime latency. At sixteen conversations, admission wait p95
is about 408 ms. This finite batch is not a steady-state throughput benchmark.

Across all profiles, 510/972 inputs exceed the diagnostic initial-context allowance. No process hit
the 512 MiB sampled abort guard or its deadline. Every profile verified exact successful replay with
no model/tool reexecution, full durable history, zero checked-out connections, no surviving async
tasks and zero remaining PostgreSQL sessions for its engine. Passing these finite profiles does not
establish a bounded-memory guarantee for arbitrary history.

## Implications for the next decision

1. Initial context demonstrably grows with durable history, although this host only needs the latest
   review command. Selection must be separate from durable transcript storage.
2. Full history materialization also grows. Selecting after reading history cannot be advertised as
   a bounded database read or constant-memory solution. A store API needs separate justification.
3. The 256 KiB tool-result profile reaches 7,914,708 bytes at a model boundary. Even with an empty
   historical context, the current tool result itself exceeds 64 KiB. Initial-context selection
   cannot enforce a universal per-model budget or safely truncate that mandatory result.
4. Admission/backpressure ownership remains with the host; these results do not justify distributed
   execution, a larger pool, a new default timeout or a new global retry policy.

The next ADR must compare host-owned selection, opt-in runtime selection, optional bounded reads and
no change. Candidate acceptance must preserve system policy, the current request, complete tool
relationships, hooks and durable replay/failure semantics; overflow of mandatory context must fail
explicitly. Three matched candidate repetitions and installed-wheel acceptance remain outstanding.
