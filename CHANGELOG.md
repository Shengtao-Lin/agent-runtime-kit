# Changelog

All notable changes to Agent Runtime Kit are documented here.

## Unreleased

## 0.4.1 - Release candidate

- Translate canonical text/message validation into safe provider failures for direct-library callers.
- Preserve unknown usage instead of inventing zeros, validate supplied non-negative integer counts,
  and merge Anthropic cumulative streaming counts without double counting.
- Reject unsupported multiple choices/candidates and nonzero candidate indices rather than merging
  or silently discarding alternatives; retain OpenAI usage-only chunks and omitted-index proxies.
- Add installed-wheel provider boundary regressions, durable failed-key/no-partial-history tests
  and checksum-pinned actual v0.4.0 preserved-state upgrade gates.
- Update Release Desk's independent lock to the actual v0.4.0 wheel and adopt checked runtime
  lifetime management, including application-body failure coverage and a public v0.4 metadata canary.

## 0.4.0 - 2026-10-21

- Add bounded installed-wheel sustained-operation probes with concurrent turns/tools, slow streams,
  replay, cancellation, backpressure abandonment and repeated managed lifetimes; CI/Release and
  lower/latest compatibility gates retain task/pool/allocation evidence.
- Add optional checked runtime lifespan management and an application-owned resource integration
  guide; injected resources remain host-owned and manual shutdown semantics are unchanged.

## 0.3.1 - 2026-10-21

- Bound decoded provider JSON/streams and SSE events, support multiline/split-UTF-8 SSE framing,
  close responses deterministically and classify safe HTTP/transport retry hints without retries.

- Reject provider streams that end without protocol completion, report stream errors, or stop
  with truncated/filtered finish reasons, preventing provisional output from becoming durable success.

- Add a separately packaged Release Desk CLI, pinned to the published v0.3.0 wheel, with its own
  business checklist, configuration, lifecycle, locked environment and published/candidate CI.
- Record actual v0.3.0 wheel fresh/upgrade acceptance and public read-only application canary.

## 0.3.0 - 2026-10-21

### Added

- Opt-in LangChain message-chunk and LangGraph messages/values incremental streaming with explicit
  custom stream mappers, node filtering, one terminal output and deterministic iterator teardown.
- Independent real framework applications, clean lower/latest dependency matrices, PostgreSQL
  pool/lock/backend fault probes and synthetic installed-library performance reports.

### Changed

- The `adapters` extra requires LangGraph >=1.2.11,<2 after an older cancellation probe revealed
  an internal pending queue task; compatibility profiles exercise the supported range.
- Runtime closes invoker streams when cancelled during downstream backpressure.
- Default framework adapters retain terminal fallback; schema `0002` and contract `v1` are unchanged.

## 0.2.0 - 2026-10-21

### Added

- Bounded runtime shutdown that tracks cancellation-shielded cleanup before resource disposal,
  plus real container in-flight SIGTERM drain/cancellation/restart acceptance in CI and Release.
- Matching app/migration image builds to avoid stale local migration artifacts during upgrades.

- Runtime/agent version span attributes and task-local request/run/thread/trace log correlation,
  with concurrent-context tests and categorical rather than raw exception export.

- Optional namespace-scoped conversation reads/appends and expiry maintenance; the runtime checks
  namespace before continuation, history, replay, or recovery. Legacy unscoped maintenance remains
  explicit and is not an authorization/RLS boundary.

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
