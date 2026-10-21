# ADR 0010: Explicit Managed Runtime Lifetime

Status: Accepted for v0.4.0 development.

## Context

The optional service, independent consumer and Release Desk each own configuration, database pools,
model clients and teardown. Their repeated `try/finally` blocks expose one recurring risk: the host
may forget runtime shutdown or ignore its false result before closing shared dependencies.
These applications do not need an agent factory or a global dependency container.

## Decision

Add optional `AgentRuntime.lifespan(shutdown_timeout_seconds=...)`. It yields the existing runtime,
then calls the existing cooperative shutdown method. It raises `RuntimeShutdownTimeoutError` when
shutdown returns false, rather than silently implying clean teardown. It validates configuration
before entering and rejects nested/concurrent lifetimes and reopening a closing runtime.

The method does not create, migrate or dispose an engine, model, store, exporter or HTTP client.
It does not drain ingress, install signal handlers, create an event loop or authorize users.
The host constructs dependencies outside this context and closes them afterwards. Existing manual
`shutdown()` remains supported with unchanged return semantics. Caller exceptions/cancellation
propagate unless teardown itself fails; normal exception chaining retains the original failure.

## Consequences

The library gains one additive lifecycle convenience, not an application framework. Explicit
dependency injection remains the default. A shutdown timeout is not a hard kill or rollback:
non-cooperative work may require host supervision and durable lease recovery. Shared resources
still require application-owned policy when cleanup is incomplete. Versioned documentation and
installed-wheel tests must cover this boundary before publishing v0.4.0.
