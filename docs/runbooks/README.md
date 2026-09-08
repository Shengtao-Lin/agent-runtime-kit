# Component Runbooks

These runbooks operate the installable `agent_runtime` library and its optional example. Start
with the component that owns the failing boundary.

| Component | Python package | Runbook |
| --- | --- | --- |
| Runtime and contracts | `agent_runtime` | [Runtime](runtime.md) |
| Model providers | `agent_runtime.models_clients` | [Model providers](model-providers.md) |
| Framework adapters | `agent_runtime.adapters` | [Framework adapters](framework-adapters.md) |
| PostgreSQL memory and runs | `agent_runtime.memory`, `agent_runtime.runs` | [PostgreSQL](postgres.md) |
| Tools and lifecycle policy | `agent_runtime.tools`, `agent_runtime.hooks` | [Tools and hooks](tools-and-hooks.md) |
| Feedback | `agent_runtime.feedback` | [Feedback](feedback.md) |
| Telemetry | `agent_runtime.telemetry` | [Telemetry](telemetry.md) |
| Optional HTTP service | `agent_runtime.service` | [HTTP service](http-service.md) |
| Containerized example | `examples.support_agent` | [Example application](example-application.md) |

Each runbook describes configuration, a health check, common failures, and a safe recovery path.
Repository verification remains centralized in `make verify` and GitHub Actions.
