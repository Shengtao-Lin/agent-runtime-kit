# Architecture

Updated for v0.6.0 publication on 2026-11-06, following the v0.4.2 review on 2026-10-28.
This describes implemented behavior unless marked
**proposed**. [The plan](plan.md) owns future work; [ADRs](adr/README.md) explain decisions;
[runbooks](runbooks/README.md) explain operation. Release evidence is not production certification.
Streaming/result and import-boundary changes labeled below ship in v0.5.0; they are absent from
the immutable v0.4.2 release.

## Product boundary

Agent Runtime Kit is an **importable execution and persistence library** for application-owned
agents. Its reusable unit is one bounded invocation with canonical input, durable outcome and
explicit lifecycle policy. It is not the agent's business logic, a replacement for LangGraph,
an autonomous-agent platform or a durable workflow engine.

The host selects models, prompts, tools, authorization, configuration and deployment. It can use
the native loop, wrap a framework, implement an invoker, or use memory independently. The wheel
contains `agent_runtime`, not examples or applications. HTTP and Docker demonstrate hosting;
neither is required to import the library. See [ADR 0012](adr/0012-library-first-evolution.md).

## Components and dependency direction

```mermaid
flowchart TD
    Host[Application composition and ingress] --> Runtime[AgentRuntime]
    Host --> Standalone[Standalone memory or feedback use]
    Runtime --> Registry[AgentRegistry and AgentInvoker contract]
    Runtime --> Hooks[Invocation-level hooks]
    Registry --> Native[NativeAgentInvoker]
    Registry --> Framework[LangChain or LangGraph wrapper]
    Native --> Model[ModelClient and optional provider adapters]
    Native --> Tools[ToolRegistry and configured tool hooks]
    Framework --> Workflow[Application-owned runnable or graph]
    Workflow -. explicit integration .-> Tools
    Runtime --> Stores[MemoryStore and RunStore contracts]
    Stores --> PG[(PostgreSQL)]
    Standalone --> PG
    Runtime --> Telemetry[OpenTelemetry API]
```

Arrows show use/ownership, not an assertion that every component imports its implementation.

| Boundary | Owns | Does not own |
| --- | --- | --- |
| Models, protocols, errors | Canonical contracts and error categories | Vendor objects or universal payload validation |
| `AgentRuntime` | Routing, history, claims, deadline, invocation hooks, finalization and replay | Prompts, graph execution or resource construction |
| Native invoker | Sequential bounded model/tool loop and invocation token totals | Durable history or automatic retries |
| Framework wrappers | Input/output and optional stream translation | Framework-internal policy or persistent checkpointers |
| Provider adapters | Supported text/function-call wire subset and safe provider failures | Complete vendor parity or account configuration |
| PostgreSQL stores | Transactions, ordering, leases/fencing, replay, memory and feedback | External-effect transactions or tenant authentication |
| Host / optional service | Configuration, ingress, authorization, pools, signals and teardown | A second writer for runtime-owned conversation history |

Core imports must remain usable without HTTP, database or framework extras. Pydantic,
`pydantic-settings` and OpenTelemetry API are current core dependencies; this is not a
zero-dependency library. Optional implementations use explicit modules/lazy imports. Business
logic must not flow from examples or applications into the package.

**Since v0.5.0:** iterator closure and terminal validation live in private `_streams.py`.
Runtime no longer imports this utility from a framework-named module. No public API or additional
queue is introduced. The dated review retains the original v0.4.2 layering finding.

**Since v0.5.0:** adapter package exports load concrete implementations only when requested.
CI checks selected core modules' static imports and probes a core-only, non-editable installed wheel
outside the checkout. The probe rejects optional implementation imports, network connections,
socket binding, subprocess/thread starts and `.env` reads during the selected public imports.
It does not sandbox arbitrary extension code or prove every possible dynamic import is safe.

## Execution paths and policy coverage

All registered invokers enter the same durable runtime envelope. The native path calls the kit's
model client and tool registry. Framework wrappers call their supplied `ainvoke()` or opt-in
`astream()`; the kit does not take over graph internals.

| Policy / contract | Actual scope in v0.6.0 |
| --- | --- |
| `before_model` / `after_model` | Once around the complete invoker, despite the historical names; not each native model round |
| `before_tool` / `after_tool` | Only a configured `ToolRegistry` receiving a runtime context |
| Tool schema / timeout / iteration limit | Registry / native-loop policy, not automatically applied to framework-native tools |
| Runtime deadline and persistence | Invokers called through `AgentRuntime`, not direct component calls |
| Descriptor capabilities | Discovery metadata, not permissions or automatic feature negotiation |

The host deliberately shares hook/telemetry configuration where required. Supplying runtime hooks
does not configure tool registries automatically. Custom extensions are trusted code, not a sandbox.

Input hooks see selected history plus the complete new request when a context window is configured;
otherwise they see full history plus the request. Output hooks run before durable completion,
but stream deltas and tool-result events may already have been delivered when they block. Hosts
requiring pre-disclosure moderation must explicitly buffer/filter output; an output guardrail
is not a streaming confidentiality gate.

## One invocation and its durable outcome

1. Resolve the agent and hash semantic request fields, excluding generated request/message IDs.
2. For an existing agent-scoped idempotency key, verify namespace and request equivalence. Replay
   success; report an active conflict or unavailable failed/blocked outcome otherwise.
3. Create or verify the thread and claim its active run slot in a short transaction.
4. Transition to running, then load history. An explicitly configured context window
   selects initial input before `before_model`; the default retains all history. Only the owner
   executes hooks and agent work.
5. Apply output hooks, find the final assistant message and build the canonical response.
6. `RunStore.complete()` atomically commits the new request/output message batch and succeeded
   run/replay response. Only then can the runtime emit public `completed`.

The success path is `accepted -> running -> succeeded`; conditional failure/blocked cleanup
can settle either active state. Failed cleanup may leave active state until later lease recovery.
For ordinary completion errors, readable committed success wins during reconciliation. Cancellation
still propagates even if the transaction committed; an equivalent request can replay that success.

Keys are unique within `(agent_id, idempotency_key)`, not agent version, namespace, user or thread.
Successful replay returns the original response IDs, agent version and usage, without executing
models/tools again. Failed keys never implicitly restart execution.

### Initial context selection (since v0.6.0)

The optional `ContextWindow` from [ADR 0015](adr/0015-opt-in-initial-context-window.md) separates
execution context from durable history. It retains current-request/system messages and complete
tool exchanges, then the newest historical groups fitting an explicit canonical UTF-8 byte budget.
Mandatory overflow fails before invoker dispatch. Hooks see selected input and content-free counts;
successful finalization still stores the original current request and generated output only.
This capability ships in v0.6.0; the library default remains full-history input. Release Desk
explicitly configures 64 KiB. See [actual adoption evidence](validation/v0.6.0-publication.md).

The measured [Stage D baseline](validation/stage-d-baseline.md) justifies an initial-context bound.
Full history is still loaded and scanned; no bounded database read or process-memory guarantee is
claimed. Native tool rounds, framework mappers/checkpoints and trusted host hooks can grow/change
context after selection. Per-model token/byte policy requires a separate explicit contract. See
the [component runbook](runbooks/context-window.md) for configuration, observation and rollback.

### Incremental history consumption (development)

Stage E's [baseline](validation/stage-e-baseline.md) reproduces full-history materialization cost.
[ADR 0016](adr/0016-history-materialization-and-validation.md) adds explicit
`AgentRuntime(stream_history=True, context_window=...)` and optional `StreamingMemoryStore`.
Default reads and custom `MemoryStore` implementations remain compatible. Opt-in PostgreSQL reads
use one ordered server-cursor snapshot; incremental selection preserves every historical canonical
and tool-relationship check while retaining only potentially selected payloads. Runtime closes the
cursor before hooks and agent dispatch, including on errors or cancellation. Complete durable history
and exact replay remain unchanged. No schema migration is required.

Database scanning/transfer remain complete. Fetch batches cap rows, not bytes; individual payloads,
pending tool IDs and server work remain workload-dependent. There is no universal RSS or per-model
input guarantee. This capability is not in published v0.6.0;
[repeated independent acceptance](validation/stage-e-incremental-history.md) passed and publication
is pending. See the [history reading runbook](runbooks/history-reading.md).

## Persistence and concurrency invariants

PostgreSQL is the only shipped persistence backend. Memory is independently importable; runtime
memory and runs must share one persistence domain, normally one host-owned engine. Runtime
conversation history has one writer: no second framework memory/checkpointer or standalone append
to a live runtime thread.

- A database constraint permits one accepted/running owner per thread across agents. Live overlap
  is a conflict, not an internal work queue.
- Transactions are short; none spans a model/tool call. Mutations lock **thread, then run**.
- Owner tokens and database-clock leases fence mutations. Completion rechecks ownership after
  message flush; stale owners cannot overwrite a successor's durable result.
- Runtime leases cover invocation budget, cleanup budget and five seconds of grace. There is no
  heartbeat, indefinite renewal or library-owned recovery worker.
- Equivalent retry, new claim, explicit recovery or host maintenance can fail expired owners.
  Expiry alone does not change a row. Recovery terminates abandoned work; it never resumes it.
- Atomicity covers this invocation's new conversation batch and run/replay record only. Standalone
  memory writes, feedback, external tools and framework side effects are separate actions.

**Fencing is not exactly-once tool execution.** A cancellation-resistant tool may finish an
external action after its lease expires. Fencing prevents stale database finalization, not that
external effect. Hosts must reconcile effects and use domain-specific idempotency before starting
new work. There is no tool journal, outbox, compensation or resumable execution contract.

Namespaces filter access but are not authentication, tenant isolation or database RLS. Owner tokens
are concurrency capabilities, not authorization credentials. See [ADR 0009](adr/0009-fenced-thread-ownership.md).

## Streaming and canonical data

Provider model events become invoker events, then public runtime events. Runtime streaming uses
a 64-item queue for backpressure. Text/tool-result events are provisional. Replay returns the
stored outcome, not a saved token event log; SSE is not a resumable event service.

Framework wrappers default to terminal fallback and support explicit incremental streaming.
Built-in providers validate their supported protocols. **v0.5.0** uses one private
validator for framework streams, custom model streams consumed by the native loop, and custom
invoker streams consumed by runtime. It withholds completion until normal source exhaustion,
rejects missing/duplicate completions and later events, and closes the source on failure,
cancellation or abandonment. Sources must terminate after completion; the invocation deadline
still applies while waiting for EOF. Direct calls to arbitrary custom implementations bypass these
kit boundaries. Published v0.4.2 lacks the generic model/invoker enforcement. See
[ADR 0013](adr/0013-terminal-stream-conformance.md) and [the plan](plan.md).

Canonical content is text, tool calls and tool results, not a universal multimodal/reasoning/artifact
format. `StrictModel` rejects extra fields; it is not globally strict or immutable. Text, content
part counts and metadata have limits. **v0.5.0** validates canonical tool payloads as
finite JSON values on ordinary construction and at registry boundaries; their public annotations
remain `Any`. No universal byte limit is imposed. HTTP body and provider decoder limits are not
whole-invocation bounds, and JSON validity is not a guarantee of every storage backend's acceptance.

`ModelResult` retains tool calls in `message.content` and `tool_calls`. **v0.5.0** requires
an assistant message, unique IDs within the result and matching ordered IDs/names/JSON arguments.
Native execution revalidates and snapshots each complete model result before any dispatch or
history accumulation, including generate-only stream fallback. Invalid results cannot dispatch
an earlier valid call from the same batch. Registry output validation occurs after the handler's
effect and before after-tool hooks, then repeats before returning a detached result. It cannot undo
external effects or intercept framework-native tools. Arbitrary custom invokers bypassing canonical
construction remain outside this enforcement. See [ADR 0014](adr/0014-model-result-and-tool-payload-contracts.md)
for error categories, compatibility and migration. Published v0.4.2 lacks these checks.

Native usage sums every model call; any unknown call makes the invocation total unknown. Framework
conversion retains the last newly generated AI message that supplies usage unless an explicit
output envelope overrides it. Runtime stores the invoker's value unchanged. These are not uniformly
comparable billing totals; never add loaded historical usage again.

## Lifetime, limits and failure surfaces

One cooperative invocation budget includes lookup, claim, history, hooks, execution, partial-event
backpressure and finalization. Cleanup has a separate budget. Python cancellation cannot hard-kill
blocking code, synchronous tools running in threads or cancellation-suppressing extensions.
Downstream timeouts, admission control and process supervision remain host responsibilities.

`lifespan()` optionally checks shutdown and rejects overlapping/reopened lifetimes. It neither
creates nor closes injected engines, models or exporters. The host drains ingress, shuts down
runtime work, then closes dependencies. Provider-created clients close through their provider;
injected clients remain caller-owned. `PostgresMemoryStore.close()` disposes even an injected
engine, so it is not an isolated per-store close. See the [ownership guide](library-integration.md)
and [ADR 0011](adr/0011-managed-runtime-lifetime.md).

History loading scans the full conversation, including with incremental selection. Queue item count does not cap total history, tool
payloads, framework buffers or process memory. Pools and global concurrency are host-configured.
The context/history policies follow measured Stage D/E requirements; there is no implicit truncation
or summarization.

Built-in translated failures use stable error categories, including safe provider failures.
Unexpected Python/store/hook errors can still propagate to direct callers: do not serialize their
tracebacks. The HTTP service supplies a sanitized public envelope. A retryable hint is not
permission to repeat unknown external effects.

## Security, observability and feedback

Kit telemetry defaults to identifiers, counts and categorical outcomes, with content capture off.
Hosts own third-party instrumentation and free-form logging. Explicitly constructing
`RuntimeSettings` reads environment and optional `.env`; runtime construction does not configure
the application.

Authorization, tenant boundaries, rate limits, TLS, secrets, backups, retention and network policy
belong to the host. Sample guardrails are composition examples, not a production safety system.
Liveness and readiness differ: readiness also checks database access and schema. Migrations run
explicitly outside application startup.

Feedback is immutable evidence linked to a successful artifact, with correction chains and
paginated reads. It never updates prompts, tools, memory, routing or models. Evaluation remains a
separate system using an explicit privacy-reviewed read/export boundary, not an implicit event bus.

## Evolution and evidence

Strengthen extension contracts and host adoption before adding an orchestrator, database, scheduler
or provider abstraction. Keep protocols at demonstrated replacement boundaries, prefer explicit
composition, and extract private helpers only for concrete invariants. No microservice split or
global dependency container is needed.

Python API, canonical contract version and database schema are separate compatibility axes.
Selected signature/OpenAPI snapshots and installed-wheel tests exist; they do not cover every
symbol or dependency permutation. [Public API coverage](public-api.md), automated library/doc
guards and rollout gates protect selected boundaries; [the plan](plan.md) records remaining work.
The [dated review](validation/architecture-review-2026-10-28.md) records the evidence for these priorities.
