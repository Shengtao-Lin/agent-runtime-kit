# ADR 0001: Framework-neutral core

- Status: Accepted
- Date: 2026-09-07

## Decision

Canonical models and runtime policy do not import LangChain or LangGraph. Framework integrations
implement the `AgentInvoker` protocol under `adapters/` and translate at the boundary.

## Consequences

Applications share one stable contract and can replace frameworks. Adapters must provide explicit
mappers for framework-specific input and output shapes.

