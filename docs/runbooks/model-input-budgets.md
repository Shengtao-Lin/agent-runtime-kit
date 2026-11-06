# Host Model Input Budget Runbook

## Purpose and availability

Use this policy when an application needs to bound every explicitly integrated
model call, including calls after tools or input hooks. It is host reference code
in [Release Desk](../../applications/release_desk/src/release_desk/model_inputs.py),
validated against actual v0.7.0. It is not an `agent_runtime` export or a newly
released library feature. [ADR 0017](../adr/0017-host-owned-model-input-budgets.md)
records why existing extension points are sufficient.

The application owns byte limits, placement and model/provider mapping. The library
continues to own invocation lifecycle and durable outcomes. Initial history selection,
incremental history reads and per-model checking are three separate policies.

## Enable and verify in Release Desk

The application explicitly wraps its router by default. Unset environment variables
use 65536 message bytes and 8192 tool-definition bytes:

```powershell
$env:RELEASE_DESK_MODEL_INPUT_MAX_BYTES="65536"
$env:RELEASE_DESK_MODEL_TOOLS_MAX_BYTES="8192"
uv run release-desk review Shengtao-Lin/agent-runtime-kit v0.7.0 --key input-budget-check --stream
```

Run from `applications/release_desk` after installing its independent lock and
configuring/migrating the database as described in its [README](../../applications/release_desk/README.md).
The normal check should end with a completed event and four saved messages. A prior
successful key replays its saved response; use a deliberate new key to test a new
configuration. Do not use a new key merely to retry a failed tool effect.

Limits must be integers of at least two bytes. The complete canonical ordered lists
are JSON-encoded with all fields, IDs, schemas, compact separators, UTF-8 and
`ensure_ascii=False`. Equality passes; one extra byte fails. Empty lists cost two
bytes. These numbers do not measure provider tokens, HTTP bodies or process memory.

## Integrate another host

Adapt or vendor the application module into your host, preserving its tests and
explicit contract. Within Release Desk's installed package, composition is:

```python
from agent_runtime.adapters import NativeAgentInvoker
from release_desk.model_inputs import ModelInputBudget, budget_model

protected_model = budget_model(model, ModelInputBudget(65_536, 8_192))
invoker = NativeAgentInvoker(
    agent_id="reviewer", version="1", model_client=protected_model, tools=tools
)
```

An application importing only `agent_runtime` does not receive `release_desk`.
Choose limits from your workload; the example values are a measured synthetic host
requirement, not universal provider defaults. Keep the wrapper directly around each
protected actual delegate. It preserves generate-only clients and forwards optional
streaming; close consumed streams when abandoning iteration.

For LangChain/LangGraph, map the final node/model input first, then invoke the wrapped
delegate. See [executable integration fixtures](../../validation/model_inputs/framework_cases.py).
Checking only the runtime entry or checking before an expanding mapper is insufficient.
The fixture uses an explicitly integrated canonical model and host-owned tools; it
does not intercept arbitrary framework-native chat models or hidden nested calls.
Trusted delegates may change their own input after checking; this is not a sandbox.

## Diagnose an overflow

1. Record the safe error code and run/request/thread identifiers. The fixed public
   message is `Model input exceeds the host budget`; diagnostics contain only
   `dimension`, `bytes` and `limit`.
2. Identify whether `messages` or `tools` exceeded its independent limit. Message
   growth can come from tools, accumulated rounds or hooks; definition growth can
   come from descriptions or schemas even with a small conversation.
3. Inspect the application's routing and input construction using synthetic data.
   Do not copy prompts, tool results, schemas or credentials into an incident report.
4. Confirm the rejected delegate was never called. Earlier model calls, external
   tool effects and provisional output may already exist and are not rolled back.
5. Choose a deliberate host change: a justified larger allowance, smaller explicitly
   designed tool output, fewer selected tools, or revised routing. No automatic
   truncation, summarization or retry occurs in this policy.

| Symptom | Meaning and action |
| --- | --- |
| `host_model_input_exceeded` | Protected dispatch rejected; inspect only safe dimension/count/limit diagnostics |
| `host_model_input_invalid` | Host input could not be measured as finite UTF-8 JSON; repair the input construction or mutation |
| `context_budget_exceeded` | Initial mandatory context does not fit; use the [context runbook](context-window.md) |
| Model receives oversized input without failure | Check wrapper placement, disabled configuration and unprotected inner models |
| Stream has text/tool events but no completion | Output was provisional; inspect durable failure and existing tool effects |
| Same failed key will not run again | Expected failed-key behavior; changing budgets does not authorize repeating effects |
| Successful replay ignores newly lowered limits | Expected exact replay; it performs no new model/tool work or checking |

## Persistence and cleanup

A rejected run stores the host error code, no successful response and no successful
turn append. Previous history remains intact. Failed-key reuse cannot silently
retry tools. Successful saved replay remains identical after restart and configuration
changes. Runtime cancellation/deadline handling is unchanged, and the wrapper closes
delegate iterators that expose `aclose`.

The check serializes canonical input synchronously. The measured admitted-input
profiles pass the frozen overhead/allocation gates, but this is not a hard memory
ceiling for arbitrary inputs or an interruptible CPU budget. Use application payload
limits and resource controls where required; a message allowance alone does not
limit database scans, framework buffers or provider-generated output.

## Disable and rollback

```powershell
$env:RELEASE_DESK_MODEL_INPUT_MAX_BYTES="none"
```

This deliberately disables both dispatch checks in Release Desk. The tool limit is
ignored while the wrapper is disabled. `RELEASE_DESK_CONTEXT_MAX_BYTES` and
`RELEASE_DESK_STREAM_HISTORY` remain independent: disabling the dispatch guard does
not change history selection or reading. Restore the variables above to re-enable.
No schema migration, package downgrade or deletion of durable data is needed.

Other hosts can remove their wrapper to restore their prior behavior. Disabling
checks permits later model inputs to grow again. Saved successes replay unchanged;
failed runs remain failed. See the [adoption guide](../adopting-model-input-budgets.md).

## Reproduce acceptance and escalate

From repository root with a venv, uv and an owned disposable PostgreSQL database:

```powershell
$env:MODEL_INPUT_DATABASE_URL="postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime"
.venv/Scripts/python scripts/validate_model_inputs.py --report-dir build/model-input-check-new
```

Each report directory is immutable once a manifest exists. Add `--benchmark` for
sequential isolated local timing/allocation acceptance with no competing workload.
CI runs deterministic correctness against the actual wheel and real PostgreSQL.
The [native](../validation/stage-f-native-host-guard.md) and
[framework](../validation/stage-f-framework-boundaries.md) reports preserve limits,
failures, raw sources and observations. Share versions, safe counts and a synthetic
reproducer when escalating; no provider account is needed for these checks.
