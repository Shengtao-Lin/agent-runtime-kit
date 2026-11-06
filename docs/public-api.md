# Public API Inventory and Compatibility Review

This inventory describes v0.6.0, including the optional initial-context window. Earlier published
artifacts remain immutable. The package version, canonical `v1` contract and PostgreSQL revision `0002` are separate
compatibility dimensions. An unchanged field/signature does not imply unchanged validation behavior.

## Supported entry points

| Surface | Entry points and responsibility | Acceptance |
| --- | --- | --- |
| Runtime and routing | Root `AgentRuntime`, `AgentRegistry`; invoke/stream/shutdown/lifespan; register/get/list descriptors | Runtime/provider signature snapshot, runtime tests and installed consumer |
| Initial context (since v0.6.0) | `context.ContextWindow(max_bytes)`, `.select(history, current)`; `ContextSelection` fields/`.summary()`; optional `AgentRuntime(context_window=...)`; `ContextSelectionError`/`ContextBudgetExceededError` | Byte/pair/mandatory-input unit tests, real PostgreSQL durable acceptance, installed framework consumers and host workload |
| Canonical data | Root exports and `agent_runtime.models`: messages, tool calls/results, requests/responses, invocation input/output/context, usage, descriptors and runtime events | Canonical model and HTTP contract tests; durable JSON round trips/replay |
| Agent extensions | `adapters.base`: `AgentInvoker`, `StreamingAgentInvoker`, `AgentStreamEvent` and its text/tool/completed variants | Extension member inventory, union discriminator checks, invalid-stream regressions |
| Model extensions | `models_clients`: `ModelClient`, `StreamingModelClient`, `ModelResult`, model stream variants and deterministic client | Extension member inventory, model-result/stream/usage behavior suites |
| Concrete agent adapters | `adapters`: `NativeAgentInvoker`, `LangChainRunnableAdapter`, `LangGraphAdapter`; direct implementation modules retain the same classes | Lazy export identity, constructor/member inventory, real framework wheel acceptance |
| Providers | Explicit `models_clients.openai_compatible`, `.anthropic`, `.gemini` client classes | Existing constructor signature snapshot and installed-wheel provider protocol suites |
| Memory | `memory`: `MemoryStore`, `PostgresMemoryStore`, thread/message/memory/page models | Protocol inventory; PostgreSQL namespace, ordering and standalone-memory tests |
| Durable runs | `runs`: `RunStore`, `PostgresRunStore`, claim/record/status models | Protocol inventory; atomic completion, ownership, recovery and replay tests |
| Feedback | `feedback`: store protocol/implementation, submission/record/page; detailed aliases in `.models` | Protocol inventory; immutable/idempotent record, target and pagination tests |
| Tools and policy | `tools`: definitions/registry; `hooks`: lifecycle protocol/events/manager, guardrail protocol/decision/hook and demonstration guardrails | Extension member inventory; payload, phase ordering and native/framework policy acceptance |
| Telemetry | `telemetry`: `RuntimeTelemetry`, `TelemetryHandle`, `configure_telemetry` | Export inventory and content-safe tracing/lifecycle tests |
| Configuration | `config.RuntimeSettings`, explicitly instantiated by the host | Settings validation tests; core import probe verifies imports do not read `.env` |
| Errors | Documented `errors` exception classes/codes, plus exported guardrail block error | Provider/error and safe-service-envelope tests; direct Python calls can propagate unexpected exceptions |
| Migration and optional HTTP host | `python -m agent_runtime.migrations`, `agent-runtime-migrate`; optional service factory/contracts | Packaged migration/upgrade tests, HTTP snapshots, container startup/shutdown |

Ten module export lists are recorded in
[`public_surface.json`](../tests/snapshots/public_surface.json). The checked extension classes cover
agent/model/store/hook/guardrail protocols, registries and the three adapters. Both public getters
and methods are included. Direct documented modules in the table remain usable even when a symbol
is not re-exported at package root; this inventory does not promote imported third-party names or
underscore-prefixed helpers to supported APIs.

## What the checks prove

[`test_extension_api.py`](../tests/unit/test_extension_api.py) resolves every listed package export,
including lazy implementations, and compares selected public members' parameter names, kinds,
defaults and callable form (sync, coroutine, async iterator or property). This catches removals,
keyword-only changes and accidental `async def stream` conversions that change how a host calls
an extension. Callable defaults are marked as callable; private mapper function identities are not
frozen. Type annotation strings and Pydantic internals are not snapshotted across dependency versions.
Strict Pyright and behavior tests complement this intentionally focused snapshot.

Stream-union tests check discriminators, supported variant names, round trips and rejection of
missing/unknown types or extra fields. Existing model-result tests cover consistent tool lists,
snapshots and finite JSON; terminal-stream tests cover EOF, duplicate/missing completions, failure,
cancellation and cleanup. Policy coverage is recorded in
[the acceptance report](validation/policy-coverage-2026-11-05.md).

The consumer harness copies these API tests and their reviewed snapshot outside the checkout only
when validating a target wheel. Baseline v0.1.1 gap probes retain their historical expectations.
Fresh installs, preserved-state upgrades and lower/latest dependency profiles therefore exercise
the target's actual exports instead of resolving them through source imports. The harness retains
non-editable provenance, dependency inventories and pytest reports.

## Reviewing changes

1. Identify the affected Python call shape, canonical payload/behavior, storage semantics and
   resource ownership. State which previously valid host operation changes.
2. Add a regression or consumer scenario for the intended behavior. Compare the inventory and
   existing signature snapshots; update them only after reviewing the actual change.
3. Record migration/rollback implications for stricter validation as well as removed/renamed APIs.
   Use an ADR for a changed architectural contract. Private refactoring alone needs no public API.
4. Run source tests and the applicable installed-wheel lane. Persistence changes require real
   PostgreSQL and preserved-state upgrades. Merge only after final-commit checks pass.

Compatibility PR triggers include package manifests/locks, package source, downstream consumer and
framework fixtures, plus the public-surface test/snapshot. The Python 3.11/3.12 lower/latest matrix
retains resolved dependency inventories. This is evidence for selected supported combinations, not
a claim that every permitted version permutation or arbitrary extension is certified.
