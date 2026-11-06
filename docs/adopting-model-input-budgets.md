# Adopting the Stage F host input policy

This is an application update on the existing actual v0.7.0 library, not a library
upgrade. Python public exports, canonical contract `v1`, PostgreSQL schema `0002`,
core dependencies and default `AgentRuntime`/`NativeAgentInvoker` behavior are unchanged.
No new tag or release asset is required. Release Desk's independent lock still pins
the verified actual v0.7.0 wheel.

## Release Desk behavior change

The newer application composition explicitly wraps its deterministic router with
message and tool-definition checks before every generate/stream dispatch. Defaults
are 65536 and 8192 canonical bytes. Normal review/history/replay behavior remains
unchanged; a request that previously grew beyond those allowances now fails before
the affected model call. A tool may already have fetched metadata before a later
dispatch fails. That read and any provisional output are not undone.

Set `RELEASE_DESK_MODEL_INPUT_MAX_BYTES=none` before starting the application to
restore unprotected dispatch behavior. Otherwise set both it and
`RELEASE_DESK_MODEL_TOOLS_MAX_BYTES` to explicit integers of at least two. These
settings are separate from initial context and incremental history controls. No
database migration, cleanup or dependency change is part of this adoption.

## Other library consumers

Existing users receive no implicit guard. Review [ADR 0017](adr/0017-host-owned-model-input-budgets.md),
the [host module](../applications/release_desk/src/release_desk/model_inputs.py), and the
[runbook](runbooks/model-input-budgets.md) before adapting the reference into a host.
Do not import `ModelInputBudget` from `agent_runtime`; it is not part of that package.
Define the measured canonical boundary and independently test any framework/provider
mapping. Provider token and wire limits remain separate requirements.

## Rollout checks

1. Exercise representative success and exact saved replay with the actual installed
   library and your configured limits.
2. Reproduce cumulative tool growth and assert that the rejected delegate receives
   zero additional calls. Inspect safe durable failure codes and existing effects.
3. Test cancellation, deadline and consumer stream closure through your delegate.
4. For frameworks, verify the check after the final mapper; retain an entry-only
   negative control. Do not claim protection for models outside that boundary.
5. Confirm disable/re-enable behavior without deleting successful or failed runs.

Release Desk's external acceptance includes both overflow dimensions, post-tool
rejection, disable configuration, sixty-turn invoke/stream history, restart replay
under stricter limits and a separate read-only public metadata canary.
