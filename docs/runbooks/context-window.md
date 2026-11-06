# Initial Context Window Runbook

## Purpose and availability

Use this component when a host needs to retain the complete durable transcript while limiting the
canonical messages passed into an agent. It is available since v0.6.0 and disabled by default in the
library. See
[ADR 0015](../adr/0015-opt-in-initial-context-window.md) and the
[measured workload](../validation/stage-d-baseline.md).

The host chooses the allowance and whether historical turns may be omitted. The runtime owns the
current-request boundary, selection placement, hook observation and durable failure handling. This
component does not own model token accounting, database pagination, agent loops or summarization.

Since v0.7.0, the library also supports incremental consumption through
`select_stream` and explicit runtime configuration. See [history reading](history-reading.md) for
availability, full-scan semantics and cursor ownership. The default loading path below is unchanged.

## Configure and inspect

Pass an immutable window configuration when constructing the runtime:

```python
from agent_runtime import AgentRuntime
from agent_runtime.context import ContextWindow

runtime = AgentRuntime(
    agents=agents,
    memory=memory,
    runs=runs,
    namespace="release-desk",
    context_window=ContextWindow(max_bytes=65_536),
)
```

Omitting `context_window` or passing `None` retains the full-history behavior. Do not enable a window
without verifying that your application can answer correctly when older turns are omitted. Choose
the allowance from the host workload; 65,536 bytes is the Stage D experiment's requirement, not a
recommended universal default. The constructor rejects booleans, nonintegers and values below two.

`ContextWindow.select(history, current)` also works independently on canonical messages and returns
a `ContextSelection`: isolated message copies, byte counts, history/request counts and omission
counts. Its `summary()` contains counts only. Serialization includes every canonical field and the
surrounding JSON array, compact separators and UTF-8; Unicode bytes may exceed character count.

During runtime invocation, a `before_model` hook can inspect:

```python
summary = event.context.attributes.get("agent_runtime.context")
if summary is not None:
    omitted = summary["omitted_history_messages"]
    selected_bytes = summary["selected_bytes"]
    limit = summary["max_bytes"]
```

The same content-free counts appear as `agent.context.*` telemetry attributes when telemetry is
configured. No transcripts, tool arguments, message IDs or omitted bodies are added. This summary
is invocation context, not response metadata or a persisted per-run decision record. Replayed runs
do not rerun selection or emit a new selection summary. Host logging remains host-owned.

## Selection and execution sequence

1. Idempotent lookup/replay and fenced thread/run ownership proceed as before.
2. The memory store loads the complete ordered history. The selector scans canonical messages.
3. All current request messages and historical system messages are mandatory. Tool calls/results
   remain in complete groups, including exchanges crossing the history/current boundary.
4. If mandatory content fits, the selector adds newest complete historical groups until the next
   group would exceed the allowance. It stops there; older small groups do not fill remaining space.
5. `before_model` sees the selected messages. The invoker receives them and the safe summary;
   `after_model` retains its existing output scope. Trusted hooks/mappers can change inputs, so this
   is a selector-output bound rather than a sandbox against application extensions.
6. A successful run persists the original current request plus new output. Previously stored history
   is neither replaced nor deleted. Exact replay returns the original saved response.

Groups normally begin at historical user messages. Protected system messages retain their original
position independently. An open tool exchange prevents splitting a group until every pending call
has one matching result. A group crossing into the current request becomes mandatory in full.
Requests with multiple messages remain intact; the selector never guesses that only the last user
message is the current request.

## Failure diagnosis

| Symptom/code | Check | Recovery |
| --- | --- | --- |
| `context_budget_exceeded` | Mandatory current/system/cross-boundary exchange exceeds the configured bytes | Inspect counts and input design in the host; deliberately increase the allowance or submit a genuinely changed request with a new key |
| `context_selection_failed` | Orphan result, duplicate simultaneously pending call ID, or incomplete tool exchange | Correct the host/importer generating malformed history; do not delete transcript entries automatically |
| `idempotent_run_unavailable` on retry | The original key already failed selection or execution | Inspect the original run's error code; changing configuration does not authorize reexecution under that key |
| Agent lacks older information | Omission count is nonzero and the task depends on older turns | Increase the allowance or disable selection for this workload; do not silently claim a successful answer is complete |
| Model input exceeds the allowance later | Native tool results or framework-owned state grew after entry | Configure a separate model/tool policy if needed; the window does not truncate current tool results |
| RSS/history-read time grows | Full history is still materialized before selection | Measure the actual operating range; use host admission/input limits and plan a separate bounded-read design if required |

Selection failures occur after claim and become durable failed runs with safe nonretryable codes.
The optional HTTP service uses its existing 400 error envelope for these codes; after SSE headers,
stream failures use the existing error event. No new public stream variant is introduced.
They dispatch no invoker and append no successful transcript. Cancellation, downstream errors and
ownership loss retain their existing runtime semantics. A new runtime with a different window can
still replay a previously successful key without reexecuting tools.

## Verification and rollback

Use venv/pytest selection and persistence tests, then the independent candidate-wheel workload.
The [workload README](../../validation/workloads/README.md) gives the baseline/candidate commands;
set `--context-max-bytes 65536` only for a candidate providing this capability. Keep raw profile,
provenance and dependency reports. Verify all initial invoker inputs fit, full history still grows
as expected, exact replay avoids work, and tasks/connections are reclaimed. Compare at least three
matched repetitions; do not substitute a short CI smoke for the full comparison.

Native tools and real LangChain/LangGraph consumers exercise selected canonical entry messages.
Framework mappers, checkpoints and internal tools can create additional context outside this limit.
Do not equate initial canonical bytes with each vendor prompt or with a total process-memory bound.

Rollback by constructing subsequent runtime instances with `context_window=None`. There is no schema
migration or lost history to restore. Existing successful keys replay unchanged and failed keys stay
failed. Disabling the policy restores unbounded initial history input, so revisit host operating limits.
