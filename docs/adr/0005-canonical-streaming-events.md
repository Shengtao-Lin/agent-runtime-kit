# ADR 0005: Canonical Streaming Events

## Status

Accepted.

## Decision

Expose streaming as an optional extension at model and invoker boundaries and as
`AgentRuntime.stream()`. Public events are limited to `started`, `text_delta`, `tool_result`, and
`completed`. The completed event contains the same persisted `RuntimeResponse` returned by
non-streaming invocation.

## Consequences

Provider-native event objects never enter the core contract. Non-streaming invokers remain valid
and produce no text deltas. Partial content is not committed as a successful response, and SSE
errors after headers are represented as safe error events.
