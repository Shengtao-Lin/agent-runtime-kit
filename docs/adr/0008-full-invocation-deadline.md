# ADR 0008: Full Invocation Deadline and Bounded Cleanup

Status: Accepted for v0.2.0 development.

## Context

The v0.1.1 timeout covered only agent execution. Slow hooks, database operations,
or stream backpressure could exceed the advertised total invocation budget (R4).

## Decision

Use one monotonic `asyncio.timeout` budget around lookup, thread setup, claim,
running transition, history, hooks, agent/provider/tools, partial stream event
production, and atomic completion. Do not reset the budget between phases.
For streaming, the budget starts on first iteration, not generator creation.

Only expiry of this budget becomes `InvocationTimeoutError`. Dependency timeouts
retain their original identity. External cancellation always propagates and is
recorded as `invocation_cancelled` where possible.

After failure, a separate `cleanup_timeout_seconds` budget (default five seconds)
allows conditional reconciliation. This is additional to the invocation budget.
Cleanup settles accepted/running rows, never terminal rows. Track the candidate
run ID before awaiting claim so acknowledgment loss can be reconciled without
touching the winning run of a concurrent idempotent request.

If completion committed before timeout, readable durable success wins. If cleanup
fails or times out, preserve the original error and log the categorical
`run_cleanup_unavailable` warning. Unfinished state requires lease/recovery work.

Cancellation does not enqueue an end marker into an abandoned, potentially full
stream queue. Delivery to a paused consumer after durable completion is not part
of this budget. Applications must close abandoned iterators.

## Limits and Operations

This is cooperative cancellation, not process preemption. Blocking event-loop
code, cancellation-suppressing plugins, synchronous tools in threads, and driver
rollback/connection cleanup cannot be forcibly terminated by a Python timeout.
The expected envelope is the invocation budget plus one cleanup budget and
cooperative teardown overhead, not a hard real-time wall-clock guarantee. Hosts
must configure downstream connection/statement timeouts and shutdown grace.
External tool side effects are not undone by cancellation.

No schema migration is required. Custom stores must be cancellation-safe and let
`fail_if_running()` settle accepted/running state.

## Verification

Unit tests block every asynchronous phase in invoke/stream, verify shared budgets,
bound cleanup, preserve cancellation/dependency errors, and close a full queue.
PostgreSQL tests cover timeout before/after commit and accepted cleanup.
Candidate-wheel tests cover before/after-model hooks. Published baseline probes
remain unchanged.
