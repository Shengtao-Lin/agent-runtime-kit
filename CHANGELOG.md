# Changelog

All notable changes to Agent Runtime Kit are documented here.

## 0.2.0 - Release candidate (not yet published)

### Added

- Native LangChain/LangGraph message helpers with tool-call/result and usage mapping, stable IDs,
  strict unsupported-content rejection, and exact history-prefix removal.
- Equivalent tool-capable Runnable and StateGraph examples plus HTTP/container acceptance;
  framework streaming remains an explicit non-incremental fallback.

- Database-clock leases, owner-token fenced mutations, conditional stale-run recovery, and a
  bounded skip-locked recovery sweep; recovery never automatically repeats external effects.
- Cross-process same-thread arbitration with retryable `thread_busy` conflicts.
- Revision `0002` ownership constraints/indexes and drained-worker upgrade validation. Successful
  evidence is preserved; unowned legacy active runs become `legacy_owner_unavailable` failures.

- A shared deadline covering all invocation phases, plus configurable bounded failure cleanup.
- Per-phase deadline tests and installed-wheel hook acceptance checks.
- Cancellation-safe stream shutdown with full buffers, and accepted-run cleanup after uncertain
  claim or running-transition acknowledgments.

- Atomic `RunStore.complete()` and conditional `fail_if_running()` contracts, with PostgreSQL
  message/response finalization in one transaction and commit-acknowledgment reconciliation.
- Database fault injection for rollback, cancellation, uncertain commits, ownership validation,
  and cleanup outages, plus independent candidate-wheel regression checks.
- Packaged Alembic migration resources and explicit `agent-runtime-migrate` / Python entry points
  that work from an installed wheel without a checkout.
- Fresh-database and v0.1.1 data-preservation migration checks in independent consumer CI.
- A locked independent consumer application with pytest acceptance checks against published and
  built wheels, run outside the source checkout in its own venv.
- Reproducible probes and a v0.2.0 readiness report for crash recovery, same-thread concurrency,
  atomic finalization, complete deadlines, and wheel migration resources.
- A CI consumer job with PostgreSQL and baseline/candidate report artifacts.

### Changed

- Runtime success requires atomic `complete()`; `transition(..., "succeeded")` is rejected.
  Custom stores must implement owner-fenced mutations, atomic same-thread claim, and conditional
  `recover()`. This is a breaking store-protocol change; see the v0.2.0 upgrade guide.
- Schema head and default HTTP readiness advance to `0002`. Old workers must be drained before
  upgrading; mixed-version rollout is unsupported.
- Invocation timeout now covers the complete flow. Conditional cleanup has a separate additional
  budget (default five seconds); it is cooperative, not a hard real-time bound.
- Release publication now requires exact-wheel fresh/upgrade validation, and manual release
  verification skips all publishing jobs.

## 0.1.1 - 2026-10-11

### Fixed

- Serialize agent capabilities in stable sorted order so durable replay structurally matches the
  original HTTP response.
- Report the installed package version in the HTTP OpenAPI document.

### Documentation

- Add an end-to-end user runbook and expand every component runbook with setup, workflows,
  diagnostics, recovery, and security guidance.
- Correct feedback offset pagination, namespace boundaries, and streaming extension guidance.

## 0.1.0 - 2026-09-08

Initial public MVP release.

### Added

- Installable, typed `agent_runtime` library with a versioned canonical contract.
- Durable runtime orchestration, idempotent replay, PostgreSQL conversation history, long-term
  memory, and append-only feedback.
- Native, LangChain Runnable, and LangGraph agent adapters.
- OpenAI-compatible Chat Completions, Anthropic Messages, and Google Gemini model adapters.
- Canonical Python streaming and optional Server-Sent Events endpoint.
- Typed tool registry, lifecycle hooks, demonstration guardrails, and bounded execution.
- OpenTelemetry instrumentation with content capture disabled by default.
- Optional fictional support-agent service and hardened containerized local stack.
- Pytest unit, PostgreSQL integration, wheel-install, and container smoke-test CI gates.
- Component runbooks and architecture decision records.

### Known limitations

- PostgreSQL is the only persistence backend.
- Streaming is text/tool-result oriented and does not expose provider-specific event types.
- Provider adapters intentionally cover the shared runtime contract, not every vendor feature.
- Authentication, authorization, rate limiting, TLS, backups, and secret management belong to the
  hosting application.
- PyPI publishing is not enabled; release wheels are attached to the GitHub Release.
