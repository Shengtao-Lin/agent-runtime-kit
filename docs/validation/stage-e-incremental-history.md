# Stage E Incremental History Acceptance

Date: 2026-11-06. The column-row candidate passes the frozen workload and independent correctness
acceptance. This is pre-publication evidence: final report-commit checks, merge, release and actual
Release Desk adoption remain separate gates. No production SLO or universal memory bound is claimed.

## Requirement, candidates and provenance

The [specification](stage-e-workload.md) was frozen in `d587d1f` before measurement. The
[initial actual-v0.6.0 baseline](stage-e-baseline.md) reproduced excessive long-history working set
and responsiveness misses. [ADR 0016](../adr/0016-history-materialization-and-validation.md)
compares alternatives and preserves both rejected candidates: batching while returning a full tuple,
and incremental selection with ORM entities. The latter passed memory gates but missed one
per-repetition first-text p95 at 2.054 seconds; its pooled passing latency was not used to waive it.

The accepted implementation adds explicit `stream_history=True`, optional `StreamingMemoryStore`
and `ContextWindow.select_stream`. Default materialized reads remain unchanged. PostgreSQL consumes
all message columns from one ordered cursor snapshot in 256-row batches, then uses the same canonical
validation. Incremental selection preserves global system messages, current input, whole tool
exchanges, original order and all historical validation. It never rewrites durable history.

| Artifact | Identity |
| --- | --- |
| Actual v0.6.0 baseline wheel | SHA-256 `abe01feae67feab23f5a5625a3a19f99914bd24e10d87da711f14a119e027798` |
| Accepted development wheel | SHA-256 `72c9a06618f2fe691ba5997d751b6f0d3eaa978d64eb0b0174e46505ee69b347` |
| Candidate package source | All 60 package files byte-match `cf81e9e8d361b56302ba232a5794a6eb6f7951dc` |
| Workload environment | Windows 11, Python 3.12.10, PostgreSQL 16.15; locked SQLAlchemy 2.1.8, Psycopg 3.3.8, Pydantic 2.14.3 |
| Initial canonical window | Explicit 65,536 bytes in both baseline and candidate |

The development wheel still carries metadata version 0.6.0; its different hash distinguishes it from
the immutable published baseline. It is not a replacement release asset. External non-editable
venvs supply the implementation; no source checkout is on the consumer import path. Matched manifests
verify identical measurement sources, dependency inventories, environment and effective history.
The archive includes the exact measured source bytes, including local line endings, and package-file
digests. Root profiling and independent consumer locks are separate from this workload lock.

## Three-repeat comparison

Both baseline and candidate completed 42 fresh-process profile/lane runs and 510 measured
continuations: 420 timing samples plus 90 diagnostic samples. Every candidate repetition passes the
absolute host budgets, and both short-history regression gates pass. The comparator reports
`accepted`; it recomputes budget outcomes from raw measurements instead of trusting recorded flags.

Timing-lane p95 below pools three repetitions for readability. RSS is the largest per-process
measurement increase over post-seed/GC RSS; per-repetition absolute gates remain authoritative.

| Initial turns / concurrency / operation | Baseline p95 (ms) | Candidate p95 (ms) | Baseline RSS increase (MiB) | Candidate RSS increase (MiB) |
| --- | ---: | ---: | ---: | ---: |
| 100 / 1 / invoke completion | 50.6 | 51.4 | 2.2 | 0.8 |
| 100 / 1 / stream first text | 42.9 | 36.0 | 2.2 | 0.8 |
| 1,000 / 1 / invoke completion | 184.1 | 209.2 | 26.6 | 1.0 |
| 1,000 / 1 / stream first text | 160.5 | 195.4 | 26.3 | 1.1 |
| 10,000 / 1 / invoke completion | 2,075.7 | 1,477.3 | 259.2 | 0.9 |
| 10,000 / 1 / stream first text | 1,959.2 | 1,472.6 | 258.7 | 1.0 |
| 1,000 / 4 / stream first text | 668.8 | 643.4 | 56.2 | 3.5 |

Candidate 10,000-turn invoke p95 is 1.477/1.458/1.469 seconds in the three separate repetitions;
stream first-text p95 is 1.427/1.473/1.472 seconds. All are below two seconds. At 100 turns, invoke
51.4 ms stays below the baseline-derived 70.6-ms threshold; stream 36.0 ms stays below 62.9 ms.
The 1,000-turn single-conversation candidate is slower than the baseline, while still meeting the
absolute budget. This change addresses the measured long-history gap, not a universal speedup.

At 10,000 turns, diagnostic Python peaks are at most 1.52 MiB and RSS increases at most 3.78 MiB,
both below 128 MiB. Absolute candidate RSS peaks are about 78.6 MiB in timing and 80.9 MiB in
diagnostic processes. A roughly one-MiB increase is not the process's total memory consumption.
The largest selected initial input across the full matrix is 65,479 bytes. Full transcript counts,
exact replay without new work, stopped RSS samplers, zero surviving async tasks, zero checked-out
connections and zero tagged database sessions pass in every raw report.

## Diagnostic interpretation and remaining costs

The 10,000-turn invoke diagnostic has pooled p95 total time 6.743 seconds, history consumption
4.763 seconds, canonical conversion 1.576 seconds, driver JSON decode 0.972 seconds and selection
remainder 1.974 seconds. These are instrumented observations, not responsiveness acceptance.
Read time contains conversion/decode; p95 values and nested intervals must not be added. Incremental
selection timing subtracts its nested awaited read time and is not an independent CPU profile.

Cursor-event p95 is about 1.24 ms and covers opening/initial execution, not later fetch calls.
Separate PostgreSQL EXPLAIN results describe server work; they do not isolate transport latency.
The driver records all JSON values and canonical conversion records every historical row. Logical
JSON sizes are not wire-byte counts. Timing runs omit tracemalloc and per-row/SQL diagnostic probes.

Every history row is still scanned, transferred and validated. Row batches are not byte limits;
one large decoded/serialized message, pending tool IDs, database sorting and snapshot lifetime
remain workload-dependent. The initial window does not constrain later model calls, framework
checkpoints or host hook mutations. Sampled RSS may miss short spikes. Sequential runs share a
disposable database whose accumulated data, physical layout and cache can change; ordinary host
noise also remains. These finite offline results establish the frozen engineering acceptance only.

## Correctness, compatibility and resource acceptance

| Boundary | Evidence |
| --- | --- |
| Complete selection semantics | Seeded differential tests against unchanged synchronous selection; systems, Unicode, oversized suffix barriers, current-boundary exchanges, reused closed IDs and detached output |
| Omitted malformed history | Old orphan/duplicate/incomplete exchanges still fail; later canonical corruption retains legacy read-error precedence |
| Real cursor ownership | One snapshot across fetch batches and concurrent append; scoped reads, early close, cancellation/timeout, same-thread conflict and no retained connection/session |
| Stored-record identity | Streamed rows match full `StoredMessage` values, including metadata, sequence, timestamps and canonical message identity |
| Durable behavior | Original full history retained, safe failed runs, exact restart replay and no repeated model/tool work |
| Extension compatibility | Default `get_messages` path unchanged; unsupported explicit mode rejected at construction; native and real LangChain/LangGraph installed consumers |
| Source and installed wheel | 758 source tests; 33 focused database cases after the column-row change; 176 actual-v0.6.0 upgrade consumer cases, four historical readiness probes deselected, no skips |
| Upgrade and soak | Source context omission, changed-policy replay, mandatory overflow and incremental continuation; 30-second shared-engine soak, 90 cycles/180 turns, no residual connections |

The [consumer archive](evidence/stage-e-column-consumer/raw.tar.gz) retains installed provenance,
dependencies, upgrade-source digest, pytest and soak evidence. CI includes explicit incremental
history smoke, existing materialized workload coverage and all earlier upgrade sources plus actual
v0.6.0. Relevant lower/latest Python 3.11/3.12 compatibility and published/candidate application lanes
remain. Smoke does not replace this full comparison.

At source `cf81e9e8d361b56302ba232a5794a6eb6f7951dc`,
[CI](https://github.com/Shengtao-Lin/agent-runtime-kit/actions/runs/42177498147),
[compatibility](https://github.com/Shengtao-Lin/agent-runtime-kit/actions/runs/42177498101),
[application](https://github.com/Shengtao-Lin/agent-runtime-kit/actions/runs/42177498150) and
[release rehearsal](https://github.com/Shengtao-Lin/agent-runtime-kit/actions/runs/42177498170) passed.
The report/evidence commit still requires its own final-head checks before merge.

## Retained evidence and promotion

- [Accepted raw archive](evidence/stage-e-column-accepted/raw.tar.gz): 42 raw profiles, manifest,
  installed audit, pytest controls, summary/comparison, package source identity and measured sources.
- [Candidate summary](evidence/stage-e-column-accepted/summary.json),
  [full matched comparison](evidence/stage-e-column-accepted/comparison.json) and
  [checksums](evidence/stage-e-column-accepted/checksums.json).
- [Matching actual-v0.6.0 baseline](evidence/stage-e-v060-matched/raw.tar.gz),
  [summary](evidence/stage-e-v060-matched/summary.json) and
  [checksums](evidence/stage-e-v060-matched/checksums.json).
- [Rejected incremental comparison](evidence/stage-e-stream-rejected/raw.tar.gz), earlier batching
  and exploratory archives, and the separate source profile are linked from ADR 0016.

The accepted raw archive SHA-256 is
`290936127370a35ff0212d5723f4c3a4c13884ebc95a8ecbef86fca44a9c62bb`;
the comparison SHA-256 is `4add0fcf2167ff2b5cb21cae27c13e52e20e6b0471f1365dfea6f72ff8ff52fe`.
No failed report or frozen threshold was replaced. All tests use venv/pytest and real disposable
PostgreSQL; no paid model endpoint was involved.

The public additions warrant a compatible minor release after final-head/main checks. Canonical
`v1`, schema `0002` and dependency ranges remain unchanged. Verify actual published package bytes
against this accepted implementation, then validate actual assets and independent Release Desk
adoption before declaring Stage E complete. Keep the host-owned
[history-reading operating limits and rollback](../runbooks/history-reading.md).
