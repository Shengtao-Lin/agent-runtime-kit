# Initial Specification Closure

Status: Initial-spec closure shipped in v0.2.0; subsequent main changes prepare v0.3.0.
This report reconciles the initial feature goals and the concrete gaps found in the pre-release
audit. It does not treat green unit tests as proof of arbitrary production readiness.

## Product Capabilities

| Initial capability | Implementation and acceptance |
| --- | --- |
| Importable framework-neutral runtime | Canonical `v1` Python/HTTP models, registry, minimal wheel imports, independent installed-wheel consumer |
| Native, LangChain and LangGraph | Native tool loop, real Runnable loop and compiled StateGraph model/tool routing; HTTP/container tests exercise common tools and contracts |
| Shared PostgreSQL state | Standalone conversation/long-term memory, packaged migrations, run/idempotency records, immutable feedback and pagination |
| Policy and observability | Typed tools, four lifecycle phases, composable guardrails, versioned safe spans and task-local operational correlation |
| Execution and operations | Optional HTTP service, non-root image, explicit matching migration image, limits, readiness, graceful resource teardown and real in-flight SIGTERM acceptance |
| Added after the initial MVP | Python/SSE streaming, three provider adapters, component runbooks, isolated wheel validation, atomic finalization, complete deadlines and fenced recovery |

## Closed Audit Gaps

### Framework Messages and Equivalent Tools

`adapters/framework_messages.py` translates public System/Human/AI/Tool message types, text blocks,
tool links/results and usage. UUIDs survive round trips; arbitrary framework IDs are deterministically
mapped. Exact input-history prefixes are stripped to avoid duplicate persistence. Unsupported
content, partial chunks and invalid calls fail closed, rather than silently losing content.
`test_framework_messages.py`, HTTP integration and Docker smoke exercise real framework objects
and the same shared model/tools. Core imports still do not require framework packages.

### Explicit Namespace Contracts

Conversation lookup/read/append and expiry maintenance accept `namespace=`. Runtime continuation,
history and replay always check its configured namespace, including before recovery. Tests verify
foreign IDs cannot reveal history, append messages, replay responses or recover another scope's run.
Omitting the optional argument deliberately preserves trusted legacy unscoped maintenance access.
This is a filtering contract, not authentication, tenant authorization or database RLS. Keys remain
agent-scoped; applications must use separate agent/key domains or stores when isolation requires it.

### Observable, Content-Safe Runtime

Kit spans carry runtime version; runtime spans include agent version/framework and safe identifiers.
Lifecycle logs capture incoming request/run/thread/trace IDs at emission time. Task-local context is
restored across success, exceptions, cancellation and nested/concurrent tasks. Caller metadata is
not promoted. SDK exporter tests and real runtime integration verify identifiers and ensure private
exception sentinels are not emitted. Default errors are categorical, not raw chained exceptions.
Hosts and third-party instrumentation still own their free-form logging/queue privacy configuration.

### Real In-Flight Container Shutdown

`docker_shutdown_smoke.py` waits for a durable running request inside a delayed synthetic model,
sends real SIGTERM, checks exit code/OOM status and database evidence, then restarts and retries.
The generous-grace case commits one response and four messages; the short-grace case records
`failed / invocation_cancelled`, no response and zero partial messages. Success replays after restart;
the failed key is not automatically re-executed. The test exposed ASGI cancellation interrupting
cleanup, fixed by tracked independent bounded cleanup tasks plus `runtime.shutdown()` before
engine/model/telemetry disposal. CI and Release both execute the acceptance path.

## Explicit Boundaries and Implementation Choices

- By default LangChain/LangGraph wrappers use terminal streaming fallback (`started`, `completed`),
  not vendor/framework-specific incremental events. True incremental native/provider streaming
  remains supported. The v0.3.0 candidate adds opt-in framework incremental translation; see the
  framework runbook for supported shapes and explicit stream mappers.
- The original recommendation of Testcontainers is implemented with real PostgreSQL 16 services
  in CI and Compose locally. Repository behavior is not tested with mocked SQLAlchemy or SQLite.
- Canonical schema/resource layouts refine the original sketch; revisions `0001` and `0002` and
  the preserved-data upgrade path are explicit. Stop old workers before upgrading to `0002`.
- Timeouts and shutdown are cooperative. A forcibly killed process, unavailable database or
  non-cooperative extension may still require lease expiry and explicit recovery. Tool effects
  are never promised exactly-once or undone by a database transaction.
- Live-provider certification, broad dependency matrices, benchmarks, RAG, evaluation systems,
  schedulers, multi-database support, UI and hosted/multi-tenant controls are not this closure's claim.

## Verification References

- [Independent wheel validation](independent-consumer.md)
- [Framework adapters](../runbooks/framework-adapters.md)
- [Runtime shutdown](../runbooks/runtime.md#application-shutdown)
- [Container acceptance](../runbooks/example-application.md#in-flight-sigterm-acceptance)
- [Upgrade procedure](../upgrading-to-v0.2.0.md)
- [Release gates](../runbooks/release.md)
