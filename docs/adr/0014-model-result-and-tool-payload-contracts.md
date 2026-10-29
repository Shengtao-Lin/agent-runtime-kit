# ADR 0014: Consistent Model Results and Finite JSON Tool Payloads

- Status: Accepted; implemented in development source, not published v0.4.2.
- Date: 2026-10-29

## Context

`ModelResult` carries tools both as assistant message parts and as a dispatch list. Built-in HTTP
providers construct both together, but custom implementations could disagree about IDs, order,
names or arguments. Native execution trusted the dispatch list, allowing executed tools to differ
from recorded history. Mutable models and unvalidated Pydantic copy/construction also bypass a
construction-only check. Forty-eight failing public-protocol cases reproduced these gaps.

Tool payload fields use `Any`. Serialization can coerce Python-only values or accept non-finite
numbers that downstream JSON consumers reject. A failure discovered only during persistence is
too late to prevent an invalid argument from reaching a tool.

## Decision

Keep both public `ModelResult` fields. Its message must have the assistant role. Extract tool-call
parts in message order; the list must match `tool_calls` in length, ID, name and arguments. IDs
must be unique within that result, not globally unique across all model rounds. Text can appear
between tool parts. Different calls may share a name or arguments; never deduplicate them.

Arguments compare by their deterministic JSON representation: object key order is irrelevant,
array order matters, and primitive representations are preserved (`true`, `1` and `1.0` differ).
Empty objects are valid. There is no inference, list reconciliation or precedence rule.

Validate on ordinary model construction. Native consumption rechecks the original result, then
reconstructs a detached canonical snapshot before usage accumulation, any tool dispatch or
generated-history accumulation. Do this for `generate`, incremental streaming after normal EOF,
and generate-only streaming fallback. Check the whole model result before the first tool in that
result; a failure in a later model round cannot undo tools from earlier rounds.

Canonical tool arguments/results accept only plain JSON values: null, booleans, strings, integers,
finite floats, lists and string-keyed dictionaries. Reject NaN/infinities, cycles, non-string keys,
tuples, sets, bytes and arbitrary Python objects instead of serializing them implicitly. Repeated
references to acyclic containers are allowed. Ordinary `ToolCall`, `ToolResultContent` and
`ToolResult` construction applies these checks without changing their field names or wire shape.

`ToolRegistry` also checks incoming arguments before its input schema, hooks and handler. The
declared Pydantic input schema may still produce application-specific typed handler values. Check
handler output before after-tool hooks, recheck after hooks, and return a detached result snapshot.
An invalid output is discovered **after** the handler's side effect, which is not rolled back.
No automatic retry or new exactly-once guarantee is introduced.

## Errors, scope and compatibility

Ordinary direct model construction raises Pydantic `ValidationError`. Native boundary validation
failures, including validation raised inside a custom provider, become safe non-retryable
`ProviderError`. Registry argument failures use `ToolValidationError`; invalid outputs use
non-retryable `ToolExecutionError`. Public messages omit payloads; exception causes remain internal.

This tightens accepted values. Custom providers must populate both call representations from the
same ordered calls; hosts must explicitly convert Python-specific outputs to JSON values. The
kit does not silently stringify values, pick one conflicting list or repair old stored data.
Signatures, canonical `v1` field shape, package dependencies and database schema `0002` remain
unchanged. Existing published artifacts stay immutable. Release version selection must acknowledge
this compatibility change; reverting the source would restore the old permissive behavior.

These are cooperative component boundaries, not a sandbox or a deep-immutability guarantee.
Arbitrary custom invokers, framework-native tools and callers bypassing constructors are not
universally intercepted. Framework conversion uses the canonical constructors, but a framework
tool may have already executed before conversion. Metadata semantics are unchanged. JSON validity
does not guarantee every storage backend accepts every value, nor bound payload bytes, nesting
capacity or invocation memory. Host-specific limits remain Stage D work.

Framework usage keeps its existing last-new-AI-message-with-usage semantics, with an explicit
envelope override including `None`; it is not silently changed to native whole-invocation totals.
Loaded history is excluded, explicit zeros survive, and absent counts are not inferred.

## Evidence

- Public custom-model fixtures cover mismatched lists/fields/order, duplicate IDs, wrong roles,
  object mutation, unvalidated copy/construction, safe errors and valid multiple-tool dispatch.
- Public tool fixtures cover JSON values, cycles, validation timing, hook mutation and detached
  return values. Provider wire fixtures reject non-finite arguments in generate and stream modes.
- Separate framework fixtures protect historical usage exclusion, last-new-message semantics,
  explicit zero and envelope overrides. Existing native fixtures retain aggregation semantics.
- PostgreSQL regressions verify failure status, no successful response/history, and failed-key
  replay without reexecution, including an invalid output after a synthetic external effect.
- The custom model and tool fixtures run against a non-editable wheel outside the checkout.
