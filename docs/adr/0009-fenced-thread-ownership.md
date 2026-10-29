# ADR 0009: Fenced Thread Ownership and Explicit Crash Recovery

Status: Accepted; implemented in v0.2.0. Extends ADRs 0007/0008 and supersedes ADR 0007's historical
run-first lock order with thread-first mutation locking.

## Context

R2 showed two concurrent requests consuming stale history on one thread. R1 showed
that hard process termination left a run permanently running. Process-local locks
cannot resolve either issue across workers, and holding a transaction across a
model call would exhaust connection pools and obstruct recovery.

## Decision

PostgreSQL claims a run and the thread's active slot in one short transaction.
A partial unique index permits at most one accepted/running row per thread,
independent of agent ID. A live overlap raises retryable `ThreadBusyError` (HTTP
409). Accepted turns read history only after acquiring this slot. Equivalent
idempotent retries still replay success or report `run_in_progress`.

Every new run has a caller-generated random owner token and a database-clock
lease. Runtime leases last the invocation budget plus cleanup budget plus five
seconds of grace. Bounded invocations need no heartbeat or indefinite renewal;
direct store users must choose a finite lease covering their execution budget.
Reads expose expiry for operations but exclude tokens from model serialization.
Tokens are concurrency capabilities, not an authentication boundary.

Mutation locks always follow thread then run. No transaction spans model/tool
execution. Transition and completion verify the owner and expiry; completion
checks again after message flush. Once a successor owns the slot, an old worker
cannot append messages or overwrite a result. Conditional cleanup requires its
original token and preserves terminal state.

Recovery marks expired accepted/running rows failed with `owner_lease_expired`.
It does not reuse their run ID, replace their token, or re-execute work. Recovery
occurs through an equivalent runtime retry, a new claim on the thread, an explicit
`recover(run_id)`, or application-owned `recover_expired(limit=100)` maintenance.
The batch operation skips locked threads; the library starts no background job.
Expiry alone does not mutate rows until one of these mechanisms runs. The recovery
bound therefore includes lease duration and retry/maintenance cadence.

The failed key remains unavailable. Reconcile uncertain external effects before
deliberately submitting a new request/key. Thread consistency covers runtime
turns, not unrelated standalone `MemoryStore.append_messages()` calls; applications
must not mutate a live runtime thread through a second write path.

## Migration and Compatibility

Revision `0002` adds ownership columns, an active-owner check, an active-thread
unique index, and an expiry index. Stop/drain all old workers before migration;
mixed-version rolling deployment is unsupported. Existing successful responses,
messages, feedback, and long-term memory are unchanged. Legacy accepted/running
rows have no provable owner and are settled as failed with
`legacy_owner_unavailable`; they are never silently rerun. Back up and inspect
unfinished work before upgrading. Downgrade removes columns/indexes but does not
resurrect settled runs. Readiness now expects `0002`.

Custom `RunStore` implementations must accept `owner_token` and `lease_seconds`
on claim, require that token on mutations, arbitrate thread ownership atomically,
and implement database-clock conditional `recover()`. Atomic completion and
bounded cancellation requirements from ADRs 0007/0008 still apply.

## Verification and Limits

Tests race independent pools and real child processes; kill an owner; exercise
live conflicts, accepted/running expiry, wrong tokens, stale completion/cleanup,
expiry after message flush, skip-locked sweeping, and cross-thread key races.
Installed-wheel consumers verify overlap rejection, fresh history after release,
crash recovery without model calls, and preservation of v0.1.1 upgrade evidence.
This is not distributed execution, exactly-once external effects, tenant isolation,
or a substitute for host-side database and shutdown limits.
