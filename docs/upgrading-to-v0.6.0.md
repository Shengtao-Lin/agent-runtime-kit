# Upgrade to v0.6.0

## Scope and compatibility

v0.6.0 is a release candidate until the tag's actual assets and application adoption are verified.
It adds optional `AgentRuntime(context_window=...)` and the importable `agent_runtime.context`
module. Existing calls remain valid, `context_window=None` retains full history, and canonical
contract `v1`, PostgreSQL revision `0002` and dependency ranges are unchanged.

The [Stage D evidence](validation/stage-d-context-window.md) records the development candidate's
matched workload and correctness acceptance. That candidate used pre-release metadata; it is not
the published v0.5.0 asset or a claim that v0.6.0 has already shipped.

## Opt in deliberately

```python
from agent_runtime.context import ContextWindow

runtime = AgentRuntime(
    agents=agents,
    memory=memory,
    runs=runs,
    context_window=ContextWindow(max_bytes=65_536),
)
```

The number is the Stage D host allowance, not a universal recommendation. The budget measures the
complete canonical JSON message list in UTF-8, not vendor tokens. Current-request messages,
historical system messages and required cross-boundary tool groups are mandatory. The selector adds
recent whole historical groups while they fit; it never clips message bodies or tool results.

Opt-in validation rejects incomplete, orphaned or simultaneously duplicate tool-call relationships.
Mandatory overflow raises `ContextBudgetExceededError` (`context_budget_exceeded`); malformed
relationships raise `ContextSelectionError` (`context_selection_failed`). Both are nonretryable.
They finalize the claimed run as failed without dispatch or successful transcript append. The
optional HTTP service retains its 400 envelope/existing SSE error event for these safe codes.

`before_model` observes selected input and `context.attributes["agent_runtime.context"]` counters.
If existing policy requires auditing the complete transcript, retain that auditing at a separate
host boundary before enabling selection. Tool/output hook names and execution scope do not change.
Telemetry adds content-free `agent.context.*` counts; responses gain no new mandatory field.

## Limits and operational impact

Full history is still loaded and scanned. This is not a bounded database read or process-memory
guarantee. Native tool rounds, framework mapping/checkpointing and trusted hooks can change inputs
after selection; no universal model-call or token cap is added. Large current tool results remain
whole even if subsequent model input exceeds the initial allowance.

The selector adds serialization, validation and copy cost, including when history already fits.
Measured short workloads can be slower. Use it to satisfy an explicit context requirement, with
host-owned admission, timeouts and payload controls; do not infer production throughput from tests.

## Rehearsal and rollout

1. Preserve the host lock/configuration and drain/shut down workers before replacing them. Keep
   database/side-effect recovery procedures appropriate to the deployment.
2. Install the verified target wheel in a venv with needed extras; verify package provenance and
   schema `0002` using the installed migration command. Upgrades from v0.1.1 additionally need the
   drained-worker [schema procedure](upgrading-to-v0.2.0.md). No new migration is needed from v0.5.0.
3. Run host pytest with the window disabled first. Then explicitly enable the intended budget and
   test multiple-message requests, system policy, tool exchanges, omission visibility and overflow.
4. Rehearse cancellation, downstream errors, concurrent requests to the same thread, slow consumers
   and exact restart replay. A changed policy does not reexecute successful/failed idempotency keys.
5. Use separate disposable databases for fresh installation and preserved-state upgrade:

```bash
python scripts/validate_consumer.py --wheel dist/agent_runtime_kit-0.6.0-py3-none-any.whl \
  --expected-version 0.6.0 --acceptance-only --frameworks --report-dir build/v060-fresh
python scripts/validate_consumer.py --wheel dist/agent_runtime_kit-0.6.0-py3-none-any.whl \
  --expected-version 0.6.0 --upgrade-from-release 0.5.0 --acceptance-only --frameworks \
  --report-dir build/v060-from-v050
```

Set `AGENT_RUNTIME_TEST_DATABASE_URL` to the intended disposable database before each command.
The upgrade source uses the actual immutable v0.5.0 wheel and pinned SHA-256. All earlier upgrade
lanes remain. Consult the [context runbook](runbooks/context-window.md) for full workload comparison.

## Rollback

Disable selection by constructing a new runtime with `context_window=None`; no transcript data was
deleted and no schema downgrade is required. Replaying a successful key still returns its original
response, while failed keys stay failed. New execution requires a deliberate new semantic request
and/or new key under host policy. Restoring an older package also requires removing the new imports
and constructor argument. Disabling the policy restores growing initial history input.
