# ADR 0002: PostgreSQL-only persistence

- Status: Accepted
- Date: 2026-09-07

## Decision

PostgreSQL is the only persistent memory and runtime-record backend in the MVP. Repositories expose
typed interfaces so callers do not depend on SQLAlchemy tables.

## Consequences

The project avoids inconsistent fallback semantics and tests real transaction behavior. Local and
CI integration tests require disposable PostgreSQL infrastructure.

