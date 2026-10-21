# Upgrading to v0.2.0

Status: Release candidate on `main`; the latest published release is v0.1.1.
This guide applies to the candidate wheel and the eventual v0.2.0 release.

## Compatibility Summary

| Surface | v0.2.0 behavior | Application action |
| --- | --- | --- |
| Python | Python 3.11/3.12; same core and optional extras | Keep only the extras your application needs |
| Canonical requests/responses | Contract remains `v1`; successful v0.1.1 JSON replays unchanged | Keep semantic request payloads and idempotency keys stable |
| Schema | Revision `0002`; no mixed-version worker rollout | Drain old workers and migrate explicitly |
| Successful persistence | Messages and replay response commit atomically | Use the same persistence domain for memory and runs |
| Invocation timeout | One cooperative budget for all asynchronous phases | Review dependency budgets and allow separate cleanup grace |
| Same-thread overlaps | Retryable `thread_busy`, HTTP 409 | Back off; do not start parallel accepted turns on one thread |
| Abandoned runs | Expired leases become terminal failures, never automatic reruns | Reconcile uncertain effects before deliberately using a new key |
| Custom `RunStore` | Breaking protocol changes below | Update and verify the store before construction |

Provider adapters, streaming event shapes, immutable feedback, and content-safe telemetry
retain their existing boundaries. This release does not add authentication or tenant isolation.

## Safe Deployment Sequence

1. Verify backups and identify the exact target database. Inspect unfinished runs and reconcile
   external effects under the application's policy. Rehearse on a restored/non-production copy.
2. Stop new traffic and drain/stop every v0.1.1 or older development worker. Do not run old and new
   worker versions together against the upgraded database.
3. Install the candidate wheel with `postgres` in a migration-job venv. Run:

   ```bash
   python -m agent_runtime.migrations current
   python -m agent_runtime.migrations upgrade
   python -m agent_runtime.migrations current
   python -m agent_runtime.migrations heads
   ```

   Set `DATABASE_URL` explicitly before these commands. The final revision must be `0002`.
   Migrations are shipped inside the wheel; no checkout/source archive is needed.
4. Confirm that successful responses, messages, feedback, and memory survived. Legacy
   accepted/running rows are intentionally settled as failed with `legacy_owner_unavailable`.
   They had no ownership proof; migration does not infer whether external tools succeeded.
5. Start only new workers. Default HTTP readiness expects `0002`. Run a synthetic
   invoke/continue/replay/stream/feedback canary, including two clients competing on one thread.
6. Restore traffic and observe latency, conflicts, safe error categories, and expired-run counts.
   Configure an application-owned recovery sweep if proactive recovery is required.

Repeating upgrade is a no-op. Never restamp an incompatible database or edit run status by hand.

## Custom Store Migration

`RunStore.claim()` now accepts `owner_token: UUID` and `lease_seconds: float`. It must atomically
claim both the idempotency key and the thread's active slot, using its persistence clock. At most
one accepted/running row may own a thread, regardless of agent ID. Live overlaps raise
`ThreadBusyError`; equivalent keys retain their existing response/status.

`transition()`, `complete()`, and `fail_if_running()` require the owner token. Completion must
verify ownership and lease validity and atomically persist both messages and canonical success.
`transition(..., "succeeded")` is unsupported. Conditional cleanup settles only this owner's
accepted/running state and preserves terminal results, including uncertain committed success.

`recover(run_id)` is required: conditionally settle expired owners as `failed` with
`owner_lease_expired`, without executing tools, reassigning that run, or changing terminal rows.
The PostgreSQL implementation also offers `recover_expired(limit=100)` for bounded maintenance.
Runtime construction checks required operation presence; custom implementations must also
validate signatures, transaction boundaries, cancellation, races, and clock behavior.

See ADRs [0007](adr/0007-atomic-run-finalization.md),
[0008](adr/0008-full-invocation-deadline.md), and
[0009](adr/0009-fenced-thread-ownership.md) for the full contract.

## Deadlines, Cancellation, and Recovery

`invocation_timeout_seconds` now covers lookup, claim, history, hooks, agent/provider/tools,
partial-event backpressure, and completion under one budget. `cleanup_timeout_seconds` defaults
to five additional seconds. Both must be finite and positive. Driver teardown and cooperative
cancellation add overhead; synchronous blocking code and external effects cannot be preempted.
Configure connection/statement and ingress timeouts accordingly.

A runtime lease lasts invocation budget + cleanup budget + five seconds of grace, measured by
the database clock. There is no indefinitely renewing heartbeat. Expiry is resolved on equivalent
retry, a new thread claim, or explicit recovery/maintenance; the library starts no scheduler.
Recovery latency includes that trigger's cadence. Recovered keys remain unavailable, not silently
retriable work. A new key is a deliberate new attempt only after side-effect reconciliation.

Partial stream output is not proof of successful persistence. Close abandoned iterators and retry
the same semantic request/key to resolve an uncertain commit. Already committed success replays
without model or tool calls, even if the original caller was canceled.

## Rollback

Keep old artifacts and a verified database backup. Stop traffic/new workers before rollback.
Prefer restoring the pre-upgrade database and v0.1.1 application together. A technical Alembic
downgrade removes ownership columns/indexes but does not resurrect failed runs or undo external
tool effects. Never describe it as full data/semantic rollback. Investigate and preserve new
evidence before choosing a restore point; do not automatically roll back a shared database.
