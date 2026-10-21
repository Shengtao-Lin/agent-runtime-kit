# ADR 0007: Atomic Run Finalization

- Status: Accepted
- Date: 2026-10-20

## Context

The v0.1.1 runtime appended messages and saved the successful run/response in separate transactions.
A failure between them left failed runs with committed conversation messages and no replayable
response. Independent consumer probe R3 reproduced this gap.

A failed commit acknowledgment also has an uncertain outcome: the server may have committed even
though the caller received an exception. Unconditionally changing the run to failed can hide a
valid stored response or replace the original exception with an invalid-transition error.

## Decision

`RunStore.complete(run_id, messages=..., response=...)` is the required successful finalization
boundary. Implementations must write the message batch, succeeded status, completion timestamp, and
canonical replay response in one transaction. The runtime must not append successful messages
separately. PostgreSQL locks the run row, validates response ownership and the final assistant
message, locks the thread, allocates contiguous ordinals, and commits all writes together.

`transition(..., "succeeded")` is rejected; callers must use `complete()`. Repeating `complete()` on
a terminal run is rejected without adding messages. Application request retries go through runtime
idempotency lookup, which returns the stored response.

`RunStore.fail_if_running()` serializes with completion using the same run-row lock and changes only
a running run to failed or blocked. It returns an authoritative record and preserves existing
terminal responses and errors. If an ordinary completion exception is reconciled to stored success,
the runtime returns that response. Cancellation still propagates, while a committed response
remains available for replay.

Cleanup failures preserve the original exception and produce a categorical warning containing the
run ID and safe error code. No raw database exception text is added to that warning. A database
outage can prevent reconciliation and leave a running record; abandoned-run recovery remains a
separate planned capability.

Standalone memory appends and completion share a PostgreSQL message-batch helper. The helper uses a
caller-owned session and flushes without committing. SQLAlchemy objects and transaction ownership
stay behind repository protocols.

## Compatibility

No schema revision is required. Existing succeeded v0.1.1 responses remain replayable. Custom
`RunStore` implementations must add both operations before adopting this development version; the
runtime checks for them at construction and has no non-atomic fallback. Memory and runs must use
the same persistence domain, normally sharing one application-owned engine.

The optional `response` parameter on `transition()` remains for a clear rejection of old success
calls. It is not a second successful write path.

## Verification

Real PostgreSQL tests inject an exception after messages have been flushed but before the run
update, cancel a transaction at that boundary, and simulate a lost acknowledgment after commit.
Invoke and streaming must retain no partial batch on rollback and replay committed success without
invoking the model again. Tests cover response ownership, repeated completion, immutable terminal
cleanup, and cleanup outages. The installed-wheel consumer has corresponding candidate checks;
the released baseline retains its historical R3 expected failure.

## Limits

This decision does not serialize whole invocations on the same thread, enforce a deadline across
persistence, add leases/fencing, or undo tool side effects. Those remain separate work. Historical
partial writes are not deleted or rewritten automatically.
