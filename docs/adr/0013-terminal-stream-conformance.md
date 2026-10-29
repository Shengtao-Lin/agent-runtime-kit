# ADR 0013: Terminal Stream Conformance at Kit Boundaries

- Status: Accepted; implemented in development source, not published v0.4.2.
- Date: 2026-10-29

## Context

Framework stream mappers rejected extra events, but native model and runtime invoker consumers
overwrote the last completed result. Custom duplicate completions or output after completion could
therefore lead to tool dispatch or durable success. Abandoning a native stream could also defer
closure of its model iterator. Seven regression failures reproduced these differences.

## Decision

Use one private framework-neutral validator and iterator-close helper. Apply it to model streams
consumed by the native invoker, agent streams consumed by runtime and framework-mapped streams.
Each consumed source must emit exactly one completion and then terminate normally. Provisional
events remain incremental; retain the completed event until EOF and source cleanup succeed.
Reject duplicate completion or any subsequent event before forwarding it. Missing completion also
fails. Close owned sources on failure, abandonment and cancellation without adding another queue.

Model protocol violations become non-retryable `ProviderError`; agent protocol violations become
`AgentInvocationError`. Framework wrappers keep their existing safe agent error category. Other
source exceptions retain existing boundary translation and cancellation semantics. No new public
error class or method signature is introduced.

## Consequences

The native loop cannot dispatch tools from an ambiguous model stream. Runtime cannot persist an
ambiguous invoker completion. Framework consumers no longer observe a provisional completed event
before a later stream error. A source that emits completion but never ends still consumes the
invocation budget and can time out without success. Extensions must finish their iterator.

Provider wire markers remain provider-specific; canonical EOF is an additional consumption contract.
Direct calls to arbitrary custom implementations are outside kit interception. Result/message
consistency, payload sizing and non-cooperative external effects remain separate work. Existing
v0.4.2 artifacts are immutable and do not acquire this behavior through a documentation update.

## Evidence

Public custom-model fixtures cover valid tool loops, missing/duplicate completion, late output,
late failure, cancellation and immediate abandonment closure. Runtime tests cover no success write,
safe errors and a source that fails to end within its deadline. Framework tests cover mapper parity.
Real PostgreSQL integration verifies failed records, no partial history and no reexecution of failed
keys. New model fixtures also run from an external non-editable candidate wheel.
