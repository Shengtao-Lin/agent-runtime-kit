# Architecture Decision Records

Accepted decisions are constraints on implementation, not evidence that every planned verification
has shipped. Read the current [architecture](../architecture.md) and [plan](../plan.md) alongside
historical ADRs. Amend decisions explicitly; do not silently treat old implementation details as current.

| ID | Decision | Current relationship |
| --- | --- | --- |
| [0001](0001-framework-neutral-core.md) | Framework-neutral core | Foundation; refined by 0012 |
| [0002](0002-postgresql-only-memory.md) | PostgreSQL-only persistence | One shipped backend; standalone memory remains supported |
| [0003](0003-otel-content-capture.md) | Content capture off by default | Host/third-party logging remains outside the guarantee |
| [0004](0004-feedback-capture-boundary.md) | Feedback separated from evaluation | No automatic learning loop |
| [0005](0005-canonical-streaming-events.md) | Canonical streaming | Extended by 0010/0013; terminal enforcement is in development source |
| [0006](0006-provider-http-adapters.md) | Direct HTTP provider adapters | Supported subsets, not full vendor parity |
| [0007](0007-atomic-run-finalization.md) | Atomic message/run completion | Extended by 0008/0009; original lock order superseded |
| [0008](0008-full-invocation-deadline.md) | Invocation deadline and cleanup | Recovery supplied by 0009; lifetime convenience added by 0011 |
| [0009](0009-fenced-thread-ownership.md) | Thread ownership and recovery | Current thread-first locking, schema 0002; recovery fails, never resumes |
| [0010](0010-framework-incremental-streaming.md) | Opt-in framework streams | Default terminal fallback remains |
| [0011](0011-managed-runtime-lifetime.md) | Explicit managed runtime lifetime | Implemented in v0.4.0; host owns dependencies |
| [0012](0012-library-first-evolution.md) | Library-first evolution | Current direction; proposed work is tracked in the plan |
| [0013](0013-terminal-stream-conformance.md) | Terminal stream conformance | Development implementation; completion withheld until source EOF/cleanup |
| [0014](0014-model-result-and-tool-payload-contracts.md) | Model result and tool payload contracts | Development implementation; consistent dispatch/history and finite JSON payloads |

The original lifetime ADR also used number 0010. Its
[old path](0010-managed-runtime-lifetime.md) is a navigation alias, not another numbered decision.
Canonical IDs are now unique; published tag documents remain unchanged.
