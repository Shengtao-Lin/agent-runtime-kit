# ADR 0004: Feedback capture is separate from evaluation and learning

- Status: Accepted
- Date: 2026-09-07

## Decision

Feedback is append-only data linked to runs, messages, or tool results. Corrections supersede older
records rather than mutating them. Recording feedback does not change runtime behavior.

## Consequences

The runtime preserves auditable evidence and a future evaluation system can consume it without
coupling evaluation domain logic to agent execution.

