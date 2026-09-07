# ADR 0003: OpenTelemetry content capture is disabled by default

- Status: Accepted
- Date: 2026-09-07

## Decision

Runtime telemetry records safe identifiers, counts, timing, and status. Raw prompts, outputs, tool
arguments, memory values, credentials, and database URLs are excluded unless content capture is
explicitly enabled.

## Consequences

Default traces are safer to export. Operators enabling content capture own the resulting privacy,
retention, and access-control obligations.

