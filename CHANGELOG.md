# Changelog

All notable changes to Agent Runtime Kit are documented here.

## Unreleased

### Added

- A locked independent consumer application with pytest acceptance checks against published and
  built wheels, run outside the source checkout in its own venv.
- Reproducible probes and a v0.2.0 readiness report for crash recovery, same-thread concurrency,
  atomic finalization, complete deadlines, and wheel migration resources.
- A CI consumer job with PostgreSQL and baseline/candidate report artifacts.

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
