# Upgrade to v0.7.0

## Scope and compatibility

This guide describes the v0.7.0 release candidate. Actual publication and independently locked
Release Desk adoption are pending; the latest verified published release remains v0.6.0. Track
[candidate readiness](validation/v0.7.0-readiness.md) and the
[accepted Stage E comparison](validation/stage-e-incremental-history.md).

v0.7.0 adds `AgentRuntime(stream_history=False)`, optional
`agent_runtime.memory.StreamingMemoryStore`, PostgreSQL `stream_messages` and
`ContextWindow.select_stream`. Existing calls remain valid. The default still calls `get_messages`
and does not require a new custom-store method. Canonical `v1`, schema `0002`, dependency ranges and
existing public imports remain unchanged. This additive capability warrants a minor release.

## Enable the capability deliberately

```python
from agent_runtime import AgentRuntime
from agent_runtime.context import ContextWindow

runtime = AgentRuntime(
    agents=agents,
    memory=memory,
    runs=runs,
    namespace="release-desk",
    context_window=ContextWindow(max_bytes=65_536),
    stream_history=True,
)
```

The host chooses the window and whether old context may be omitted. The 64 KiB value belongs to the
finite Release Desk experiment, not a library default or a token budget. History streaming applies
to both invoke and response-streaming entry points. The runtime does not read environment variables
to enable it.

The flag must be a boolean. Enabling it without a window raises `ValueError`; enabling it on an
unsupported store raises `TypeError` during construction. There is no silent fallback. A custom
implementation opting in must yield all canonical `StoredMessage` records in sequence order from
one read snapshot and release resources on exhaustion, failure, cancellation and explicit `aclose`.
Structural protocol checks do not prove a custom implementation satisfies those semantics.

Review PostgreSQL subclasses that override `get_messages`: opting in calls a different method, so
custom filtering, authorization or instrumentation must be deliberately supplied at that boundary.
Leaving the flag false preserves the existing override path. No arbitrary third-party store gains
the new behavior automatically.

## Semantics, ownership and limits

The incremental selector preserves v0.6.0's current/system retention, whole-group suffix, tool
relationships, order, detached selected messages and omission counts. It validates omitted old
exchanges too. Canonical read failures retain precedence over earlier selection errors. Mandatory
overflow and malformed relationships dispatch no invoker and become durable failed runs. Hooks
observe the same selected initial input; complete original current messages and new outputs are
persisted on success. Exact saved replay does not reread or reselect history.

PostgreSQL uses one ordered server-cursor snapshot and 256-row fetch batches, selecting all columns
without constructing an ORM entity per row. Runtime closes that cursor before hooks and agent work.
Standalone consumers must use `contextlib.aclosing`, including on early abandonment; slow direct
consumers retain a connection and transaction snapshot until closure.

All rows still cross the database/client boundary. Batch row counts do not bound bytes. A large
payload, pending tool IDs, server sort/spill and concurrent reads still need host limits. The
initial canonical window does not bound every model call, trusted mapper/hook changes, framework
checkpoints or process RSS. The measured 10,000-turn working-set improvement is not a universal
hard memory bound; some medium-history profiles are slower. See the
[component runbook](runbooks/history-reading.md) for diagnostics and operating procedures.

## Rehearsal and rollout

1. Preserve the existing host lock/configuration and drain active workers before replacement.
2. Install the verified target wheel in a venv with required extras. Check package provenance and
   installed migration revision `0002`; no migration is added from v0.6.0. Earlier v0.1.1 sources
   still need the drained-worker [schema upgrade procedure](upgrading-to-v0.2.0.md).
3. Run host pytest with the default read mode, then enable the intended window/read mode explicitly.
   Verify system policy, complete exchanges, omitted malformed rows and current-request overflow.
4. Verify cursor cancellation/timeout, same-thread contention, exact process-restart replay, full
   retained transcript and connection/session cleanup. Configure admission/pools from the workload.
5. Rehearse actual published-state upgrades in separate disposable databases:

```bash
python scripts/validate_consumer.py --wheel dist/agent_runtime_kit-0.7.0-py3-none-any.whl \
  --expected-version 0.7.0 --acceptance-only --frameworks --report-dir build/v070-fresh
python scripts/validate_consumer.py --wheel dist/agent_runtime_kit-0.7.0-py3-none-any.whl \
  --expected-version 0.7.0 --upgrade-from-release 0.6.0 --acceptance-only --frameworks \
  --soak-seconds 30 --soak-shared-engine --report-dir build/v070-from-v060
```

Set `AGENT_RUNTIME_TEST_DATABASE_URL` to the intended disposable database before each command.
The source wheel is checksum-pinned to actual v0.6.0; older upgrade lanes remain. The Stage E
full comparison uses separate timing/diagnostic processes and three repetitions; CI smoke is not
equivalent evidence. Do not replace the application's independent lock until actual release assets
have been published and verified.

## Rollback

Construct subsequent runtimes with `stream_history=False`, retaining the same window. This restores
the previous complete-materialization cost and the same selection policy without deleting data or
changing schema. Successful keys replay; failed keys remain failed. If downgrading to v0.6.0, remove
the new constructor keyword, optional protocol import and direct `select_stream`/`stream_messages`
calls. Disabling the context window is a separate policy change that restores full initial input;
do not conflate it with disabling incremental reads.
