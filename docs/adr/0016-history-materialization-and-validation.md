# ADR 0016: History Materialization and Complete Validation

Status: implemented in development; final repeated acceptance and publication pending.

## Evidence and alternatives

The [three-repeat actual-v0.6.0 baseline](../validation/stage-e-baseline.md) reproduces a host gap:
10,000-turn timing RSS increases are about 259 MiB against a 128 MiB allowance, and stream first-text
misses two seconds. Diagnostic Python allocations peak at 205 MiB. The 64 KiB initial context still
passes, and every durable-history/replay/cleanup assertion passes. The problem is materialization
and cost, not missing context selection.

| Alternative | Evaluation |
| --- | --- |
| Keep v0.6.0 unchanged | Fails the frozen 10,000-turn host requirement; remains the comparison baseline |
| Rotate host conversations | Useful when the host allows independent conversations, but changes the requested continuous audit-thread workflow |
| Batch the existing complete-history read | Tested and rejected: a complete returned tuple still exceeds frozen memory and responsiveness budgets |
| Optional incremental history consumption and selection | Selected for acceptance: retain complete validation while avoiding a complete canonical transcript in memory |
| Selective latest-history reads | Cannot simply omit old rows: global system retention and malformed-exchange validation must remain correct |
| Metadata/index maintenance | Consider only with evidence; adds writer consistency, migration/backfill and recovery obligations, and does not by itself remove Python materialization |

## Rejected batching-only candidate

One exploratory repetition used a server-side cursor with 256-row batches but retained the complete
tuple return. At 10,000 turns, invoke p95 was 2.497 seconds and timing RSS increased 158.9 MiB;
diagnostic Python peak was 148.8 MiB and RSS increased 309.8 MiB. Stream first-text p95 was 2.092
seconds. This misses the frozen two-second/128-MiB requirements. Its original implementation patch,
manifest, raw results and summary are retained in the [failed probe archive](../validation/evidence/stage-e-batched-probe/raw.tar.gz),
with [checksum](../validation/evidence/stage-e-batched-probe/checksums.json). The default method was
restored; do not promote this candidate or silently loosen the budgets.

## Decision and compatibility

Add `StreamingMemoryStore.stream_messages(thread_id, namespace=...)` as an optional protocol,
`ContextWindow.select_stream(history, current)` as an async selector, and the explicit runtime
keyword `stream_history=False`. Opting in requires both an explicit window and a compatible store;
construction rejects missing capability instead of silently falling back. Default invocation still
uses the unchanged `get_messages` and synchronous selector. Existing custom stores require no new
method. A PostgreSQL subclass overriding `get_messages` must review/implement the new method before
opting in; enabling it intentionally selects a different extension entry point.

PostgreSQL streams one sequence-ordered SELECT through a server-side cursor, with 256-row ORM
batches and one statement snapshot. Concurrent appends after cursor opening do not enter that
snapshot. This is not independent offset/keyset queries or a resumable pagination token. Namespace
checks retain their previous behavior. The async generator owns its session/cursor and closes both
on exhaustion, explicit close, failure or cancellation. Runtime owns consumption and closes it
before hooks, agent dispatch and durable writes. Standalone consumers must use `contextlib.aclosing`.

The incremental selector validates every canonical row and every tool relationship. It retains
mandatory system/current content, the current open group and the potentially selected suffix of
complete optional groups. Oversized groups form a suffix barrier; older small groups cannot fill
their place. Systems preserve original order; cross-request tool groups remain mandatory. Payloads
that cannot fit may be released while their validation/accounting state remains. The unchanged
synchronous selector is the differential-test reference. Selected messages are detached copies.

Relationship/serialization errors are deferred until the store scan ends, preserving the legacy
priority of canonical read failures over selection errors. Mandatory overflow is reported after
relationship validation. Malformed omitted history cannot become a successful invocation. History
is never rewritten; successful runs persist original current messages and new output, and saved
responses replay without reading/reselecting history. Database schema remains `0002` and the
canonical contract remains `v1`. Roll back by disabling `stream_history`; no data migration is needed.

## Resource boundaries and measurement

[SQLAlchemy's yield-per contract](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#fetching-large-result-sets-with-yield-per)
supports batching driver/ORM materialization; [async results](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncResult)
require explicit closure on incomplete consumption. A row-count batch is not a byte bound: individual
rows can be large. The selector no longer retains a complete transcript, but a single decoded/serialized
message and pending tool IDs can still grow. The database scan, transferred rows and validation work
remain O(history); statement snapshots and server sort/spill costs remain real. There is no universal
RSS, wire-byte, token or per-model-call bound. The host still owns payload limits, admission,
timeouts, pool sizing and conversation lifecycle. Diagnostic cursor-execute time covers opening/initial execution, not subsequent
server-cursor fetch calls; total read and driver JSON decode still cover the full consumption.

The incremental diagnostic wrapper times awaiting rows separately from selector work; the reported
selection remainder subtracts those nested read waits. It is not an independent CPU profile. Timing
lanes omit those probes and tracemalloc. RSS increase is measured against post-seeding GC, not an
empty process; absolute peaks are also retained. Initial and later sequential runs can differ in
database physical layout, cache and host noise, so compare matched final repetitions, not isolated
headline values.

The [incremental exploratory archive](../validation/evidence/stage-e-stream-probe/raw.tar.gz)
([checksum](../validation/evidence/stage-e-stream-probe/checksums.json)) passes all frozen budgets in
one repetition. At 10,000 turns, timing invoke p95 was 1.617 seconds and stream first-text p95 was
1.548 seconds; measurement RSS increases were 1.2/1.3 MiB and diagnostic Python peaks about 1.9 MiB.
This is evidence to proceed, not final acceptance. Its candidate wheel carries development metadata
0.6.0 but is distinguished by its manifest hash; it is not the immutable published v0.6.0 artifact.

Final acceptance still requires three matching baseline/candidate repetitions, short-history
regression checks, real-database failure/cancellation/snapshot coverage, installed native/real
framework consumers, final-head/main checks and actual release/adoption. The public additions
require a compatible minor release after acceptance; version selection/publication is a separate step.
