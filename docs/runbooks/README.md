# Runbooks

Runbooks are organized in two layers:

1. Start with the [User Runbook](../user-runbook.md) for prerequisites, installation, configuration,
   end-to-end workflows, common commands, and first-response troubleshooting.
2. Use the component runbook that owns the failing boundary for deeper diagnostics and safe
   recovery.

The installable `agent_runtime` library is the product boundary. The fictional support agent is an
integration example and is not included in the wheel.

## Component Index

| Component | Python package | Use this runbook for |
| --- | --- | --- |
| Runtime and contracts | `agent_runtime` | [Invocation, streaming, replay, threads, and run state](runtime.md) |
| Model providers | `agent_runtime.models_clients` | [Provider setup, canaries, tool translation, and provider failures](model-providers.md) |
| Framework adapters | `agent_runtime.adapters` | [Native, LangChain, and LangGraph mapping boundaries](framework-adapters.md) |
| PostgreSQL memory and runs | `agent_runtime.memory`, `agent_runtime.runs` | [Connectivity, migrations, pools, sequencing, and cleanup](postgres.md) |
| Tools and lifecycle policy | `agent_runtime.tools`, `agent_runtime.hooks` | [Schema validation, execution, deadlines, hooks, and guardrails](tools-and-hooks.md) |
| Feedback | `agent_runtime.feedback` | [Targets, idempotency, corrections, and exports](feedback.md) |
| Telemetry | `agent_runtime.telemetry` | [OTLP configuration, trace verification, privacy, and exporter failures](telemetry.md) |
| Optional HTTP service | `agent_runtime.service` | [Endpoints, health/readiness, SSE, status codes, and gateway boundaries](http-service.md) |
| Containerized example | `examples.support_agent` | [Local exploration, Docker smoke tests, and provider canaries](example-application.md) |

## First Response by Symptom

| Symptom | Start here |
| --- | --- |
| Import or first-time setup problem | [User Runbook](../user-runbook.md) |
| `unknown_agent`, `run_in_progress`, or replay conflict | [Runtime](runtime.md) |
| Fake mode works but a real model fails | [Model Providers](model-providers.md) |
| Duplicate history or framework output mismatch | [Framework Adapters](framework-adapters.md) |
| `/readyz` fails or pool acquisition times out | [PostgreSQL](postgres.md) |
| Invalid, missing, repeated, or slow tool | [Tools and Hooks](tools-and-hooks.md) |
| Feedback target or correction is rejected | [Feedback](feedback.md) |
| Traces are missing or contain unexpected content | [Telemetry](telemetry.md) |
| HTTP status, body limit, or SSE buffering problem | [HTTP Service](http-service.md) |
| Docker or fictional demo problem | [Example Application](example-application.md) |

## Verification Baseline

All automated tests use pytest. Repository-wide verification is centralized in GitHub Actions and
the commands below:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -m "not integration" -q
```

PostgreSQL integration tests require a migrated disposable database. See the
[PostgreSQL Runbook](postgres.md) for platform-specific commands. `make verify` is a convenience on
POSIX-compatible development environments.

## Escalation Information

When a runbook does not resolve the problem, collect package/Python versions, environment type,
safe error code, run/request/trace identifiers, migration revisions, and a sanitized minimal
reproduction. Never attach secrets or customer content. File reproducible problems in
[GitHub Issues](https://github.com/Shengtao-Lin/agent-runtime-kit/issues).

## Document History

| Version | Date | Change |
| --- | --- | --- |
| 0.1 | 2026-09-07 | Added concise component operating notes for v0.1.0 |
| 0.2 | 2026-09-09 | Added a user runbook and expanded component workflows, diagnostics, and safety guidance |
