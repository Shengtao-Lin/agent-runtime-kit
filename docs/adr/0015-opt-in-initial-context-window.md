# ADR 0015: Opt-in Initial Context Window

Status: accepted; implemented and independently validated in development. Publication remains pending.

## Evidence and alternatives

The [published v0.5.0 baseline](../validation/stage-d-baseline.md) reproduces initial-context growth:
510 of 972 continuations exceed the host's 65,536-byte allowance. At 1,000 initial turns, invoker
input reaches 2.77 MB. Release Desk requires the current review command, not every previous review.
Full history reads and measurement overhead also grow; these are distinct costs.

| Alternative | Assessment |
| --- | --- |
| Keep current behavior only | Fails the measured initial-context requirement; retain as the compatible default |
| Host invoker wrapper | Can filter messages, but receives merged history/request without a trustworthy request boundary; duplicating host request tracking or guessing the latest user message risks dropping multi-message requests and policy |
| Explicit runtime context window | Runtime already owns the history/request boundary, before-model policy and durable claim; a small optional pure selector can preserve mandatory input and expose omission counts consistently |
| Bounded store reads | Needed for a database materialization bound, but safe global system retention and tool closure require a separate store/query contract; the current finite matrix does not hit its RSS/time abort guards |

Choose an optional `ContextWindow` configured by the host on `AgentRuntime`. No pluggable selector
protocol, automatic summarizer, token estimator or storage API is added. This solves initial-context
growth within the measured operating range; it does not solve arbitrary history materialization.

## Selection contract

`agent_runtime.context.ContextWindow(max_bytes=...)` uses compact UTF-8 JSON for the complete canonical
message list, with all fields, array punctuation and `ensure_ascii=False`. This is neither provider
tokens nor wire serialization. Configuration requires an integer budget of at least two bytes.

The selector receives history and current request separately. It preserves their original order,
every current-request message, every historical system message, and complete call/result relationships.
Historical groups start at user messages, excluding independently protected system messages; an open
tool exchange joins groups until all its calls have results. The request boundary also starts a group
when no tool exchange crosses it. A group touching the current request is mandatory in full.

Selection first budgets mandatory groups/system messages, then retains a contiguous suffix of the
remaining historical groups from newest to oldest. If the next complete group does not fit, selection
stops; it does not skip a newer large group to choose older smaller ones. Messages and tool payloads
are never truncated. An incomplete/orphan/ambiguous tool exchange fails explicitly when this policy
is enabled, even if that historical group would be omitted. Reuse of a call ID after its earlier
exchange has closed remains supported; simultaneous duplicate IDs are ambiguous and rejected.

Mandatory overflow raises a stable nonretryable `ContextBudgetExceededError`; malformed tool
relationships raise `ContextSelectionError`. Neither exposes message bodies or tool identifiers.
Selection reports counts and byte totals and returns isolated selected messages; durable history and
the original request remain unchanged. Omissions are visible in `RuntimeContext.attributes` and
content-free telemetry. No policy output is inserted into the caller's request metadata.

## Execution, durability and framework boundary

The default `context_window=None` preserves the current full-history path and validation behavior.
Opt-in selection happens after the fenced run claim and history read, before `before_model`. That
hook observes the selected invocation context and counts; it is not a full-history auditing hook.
The selector does not alter hook names, ordering or invocation-level meaning. Output/tool hooks retain
their existing scope. Hooks and framework mappers are trusted host extensions and may mutate inputs;
the byte limit describes the selector output, not a security sandbox against those extensions.

Selection failures finalize the claimed run as failed with their safe code; no invoker executes and
no successful transcript is appended. Successful commit still stores the original request plus new
output only. Exact replay precedes history read/selection and retains its original response, even if
the host later changes policy. Lease fencing, deadlines, cancellation and recovery are unchanged.

Native, LangChain and LangGraph receive the same selected canonical entry messages. Their loops,
mappers, checkpoints and framework-native tools remain host-owned. This window does not cap every
model call: a current 256 KiB tool result cannot fit a 64 KiB model budget merely by omitting history.
No framework-wide enforcement or implicit result truncation is promised.

## Validation and compatibility

Acceptance must cover exact-byte/UTF-8 boundaries, mandatory overflow, protected system ordering,
multi-message requests, complete parallel/cross-boundary tool exchanges, malformed history, no input
mutation, default compatibility, hook observations and replay after policy changes. Real PostgreSQL
tests must cover durable failure, cancellation, same-thread ownership and resource cleanup. Real
framework consumers verify their canonical entry boundary without claiming internal model budgets.

Run the same locked workload against an independently installed candidate wheel with three full
repetitions. Record initial context separately from model inputs and full history reads; inspect
latency/allocation overhead rather than claiming improvement from byte counts alone. Keep all existing
compatibility, public-surface, import, upgrade and release gates. The new optional Python capability
requires a minor release and independent Release Desk adoption; canonical v1 and schema 0002 remain.
Disabling the window restores the old input behavior without data migration or transcript loss.

The [candidate acceptance report](../validation/stage-d-context-window.md) records matched three-repeat
experiments, all initial inputs within budget, unchanged history-read amounts and mixed latency costs.
It also records source and installed-wheel correctness coverage. Finite workload success does not
extend the guarantee to arbitrary history or framework-internal prompts.
