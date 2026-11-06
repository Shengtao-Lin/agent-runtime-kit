# ADR 0017: Host-owned Model Input Budgets

Status: accepted design; native and explicit real-framework boundaries validated against
published v0.7.0. Final application adoption and delivery audit are tracked in Stage F.

## Evidence and alternatives

The [frozen requirements](../validation/stage-f-workload.md) distinguish complete canonical
message bytes, tool-definition bytes, provider wire requests and model tokens. The unchanged
Release Desk baseline fits the host budgets, while [focused cases](../validation/stage-f-input-growth.md)
reproduce growth after initial context selection. This does not invalidate ADR 0015/0016:
those contracts select initial context and control history materialization, respectively.

| Alternative | Assessment |
| --- | --- |
| Initial window or entry hook only | Cannot observe later tool results or framework mapping; negative controls dispatch oversized input |
| Native invoker budget parameter | Can enforce native calls but would add a library API while leaving framework-owned models unprotected |
| Universal runtime interception | Cannot see arbitrary framework-internal model calls or vendor mapping; an unsupported ownership claim |
| Host `ModelClient` wrapper and explicit pure policy | Existing public protocols cover native generate/stream/fallback; hosts can attach the same policy at each integrated framework model boundary |

Choose the last option. The [native evidence](../validation/stage-f-native-host-guard.md) meets
durability, lifecycle and frozen local overhead gates. The [framework evidence](../validation/stage-f-framework-boundaries.md)
validates real Runnable/compiled-graph execution with explicit final-mapper placement and
entry-only negative controls. No library capability, version bump or forced release is required.

## Contract and ownership

Release Desk owns `model_inputs.py`. It is reusable host reference code, not a new `agent_runtime`
export or a provider decorator supplied by the library. `ModelInputBudget` measures complete
ordered lists using JSON-mode canonical fields, compact finite JSON, UTF-8 and `ensure_ascii=False`.
Message IDs, all parts, schemas and punctuation count. Empty lists cost two bytes; equality passes.
Messages and definitions have separate explicit limits. No truncation, summarization, current-input
deletion, automatic retry or tool-result rewriting occurs.

`budget_model` preserves the optional stream capability: generate-only clients retain native's
generate fallback. Checks run immediately before delegate generation or stream opening. Opened
delegates exposing `aclose` are closed on success, error, cancellation and explicit abandonment.
Existing runtime deadlines, stream terminal validation and durable ownership remain unchanged.
Resources belong to the host; a synchronous local size check is not an independently interruptible
task or protection against arbitrary allocations. The admitted-input performance range is measured.

For frameworks, the application maps its final model input, then invokes its explicitly wrapped
delegate. The examples use real LangChain Runnable and LangGraph graph control flow with an offline
canonical model. They do not monkey-patch arbitrary framework chat models, intercept opaque inner
calls, own framework routing, or automatically apply ToolRegistry to framework-native tools.
When integrating a different model API, the host must define and test its own mapping/check/delegate
boundary. Checking before a mapper that expands the request does not protect the mapped request.

The delegate and host objects are trusted. Mutation inside a delegate after checking is outside
this boundary; this is not a sandbox. Provider mapping can change wire size and tokens. Canonical
byte acceptance never implies compliance with a vendor context limit or an HTTP payload limit.

## Failure and compatibility

Overflow uses host-owned `ModelInputExceeded`, a nonretryable `RuntimeKitError` with code
`host_model_input_exceeded`. Its public message is fixed; diagnostics contain only dimension,
measured bytes and limit. Unmeasurable JSON uses `host_model_input_invalid`, also nonretryable.
No prompt, result, schema or provider error detail is placed in those diagnostics.

Failure can follow successful model calls, tool effects or provisional output. It does not undo
those effects. Existing durable failure semantics append no successful turn and store no replayable
response. Failed-key reuse does not repeat work. Saved successful replay bypasses new policy checks,
model calls and tools even if the host later lowers its limits. Normal outputs, usage and full
successful history remain unchanged.

Library configuration and core dependencies do not change. The independent host must explicitly
enable its desired policy, document disable/rollback controls and validate actual locked-wheel
adoption. A future library proposal requires a new demonstrated reuse or ownership gap; this ADR
does not pre-authorize a generalized policy plugin system, token estimator or provider SDK wrapper.
