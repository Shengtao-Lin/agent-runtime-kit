# Development Plan

Reviewed on 2026-10-28 against v0.4.2. This is the living implementation queue, not a release log
or authorization to spend money, select credentials or change production systems. Historical
execution records are [archived separately](validation/iteration-history-through-v0.4.2.md).

## Direction and baseline

Keep a small importable runtime library that makes one application-owned agent invocation durable,
observable and explicit about failure. Preserve framework-owned execution, one conversation owner,
optional integrations and host-owned resources. See [architecture](architecture.md),
[ADR 0012](adr/0012-library-first-evolution.md) and the
[design review](validation/architecture-review-2026-10-28.md).

- Initial capability scope is implemented; see [initial-spec closure](validation/initial-spec-closure.md).
- v0.4.2 is published with atomic/fenced persistence, optional checked lifetime management,
  provider validation and native whole-invocation usage. Contract is `v1`, schema `0002`.
- The 2026-10-28 review used release commit `b4bf577` and publication documentation at `68ac428`.
  [Publication PR #33](https://github.com/Shengtao-Lin/agent-runtime-kit/pull/33) subsequently passed
  CI and merged on 2026-10-29. This revision preserves that review's evidence and current priorities.
- Release Desk still pins actual v0.4.1. Candidate v0.4.2 checks are not actual adoption.
- Existing gates cover real PostgreSQL, non-editable wheels, preserved-state upgrades, container
  shutdown and selected dependency matrices. They do not prove universal extension conformance,
  production load capacity or account-specific provider compatibility.

## Priority order

| Stage | Outcome | Depends on |
| --- | --- | --- |
| A. Close release adoption | One independent application consumes actual v0.4.2 | Verified assets and passing publication-documentation checks |
| B. Enforce extension contracts | Custom extensions obey terminal and canonical invariants | Current-state review; no paid provider needed |
| C. Guard library boundaries | CI detects import, packaging, API and compatibility drift | Public-surface inventory and focused tests |
| D. Validate one host workload | Resource and side-effect requirements drive API decisions | A named host scenario and operating envelope |

A and B can be prepared independently; keep separate PRs and acceptance evidence. Documentation
clarification does not implement B/C/D. Do not promise a version or feature release before its
compatibility impact and acceptance criteria are known.

## A. Complete actual-release adoption

1. Review/finish publication documentation without replacing existing tags or assets.
2. Update Release Desk's manifest, independent lock and provenance assertions to the actual
   v0.4.2 URL/digest. Avoid unrelated dependency upgrades.
3. Validate its own lock from an external venv; run application pytest and a read-only public
   metadata canary, cross-process exact replay, history and feedback idempotency.
4. Record provenance/results; update adoption status only after success.

**Exit:** the independent application imports no checkout/example code, consumes the published
digest, and passes its current tests and restart acceptance. Adopting a published wheel does not
by itself require another library release.

## B. Strengthen custom extension contracts

This is the highest-priority library correctness work; do it before adding more adapters.

### B1. One terminal result at every streaming boundary

- Write failing public-protocol fixtures for custom model/agent streams: missing/duplicate
  completion, events after completion, partial failure, cancellation and iterator closure.
- Use one private, framework-neutral terminal-validation pattern at model and invoker boundaries.
  Preserve closure/backpressure; do not introduce another queue at each translation layer.
- Keep provider wire completion distinct from canonical stream completion.

**Exit:** invalid streams cannot execute tools from an ambiguous model result or commit successful
history. Cover calls through kit adapters/invokers or the conformance harness, runtime durable
failure and valid framework mappers. Arbitrary direct calls to custom implementations cannot be
intercepted by the library. Document safe errors; no blanket retry policy change is implicit.

### B2. One unambiguous model/tool result

- Specify correspondence between assistant tool-call parts and `ModelResult.tool_calls`: identity,
  order, name and arguments. Add mismatched-list, duplicate-ID and invalid-role regressions first.
- Prefer shared validation retaining current fields over removing redundant fields immediately.
  If the public shape must change, record a separate ADR and migration path.
- Specify JSON-compatible tool payload semantics, including non-finite numbers. State whether
  validation occurs before a tool, after a tool or at persistence; failure cannot undo an external
  effect. Numeric size-limit defaults depend on Stage D's host measurements and are deferred until
  then; they do not block the result-correspondence or terminal-stream work.
- Test framework usage separately from native aggregation. Do not sum loaded history, infer
  missing values or silently call last-message usage a whole-run total.

**Exit:** malformed custom results fail before ambiguous dispatch or durable success. Valid empty
arguments, fragmented provider arguments and explicit zero/unknown usage retain documented meaning.

### B3. Explicit policy coverage

Keep existing hook names compatible while documenting invocation-level semantics. Test deliberate
runtime/tool hook composition; framework-native operations bypass the registry unless integrated.
A per-model/per-token hook is a separate decision, not a silent reinterpretation of existing names.

**Exit:** tests/runbooks show what is enforced and where, including provisional output before an
output guardrail. Do not claim a universal framework safety interceptor.

## C. Add architectural fitness checks

These are proposed checks; current CI does not implement the complete list.

| Invariant | Planned automated evidence |
| --- | --- |
| Minimal library imports | Core-only installed wheel imports without loading optional HTTP/DB/framework implementations or starting network/resource work |
| Correct distribution | Package/types/migrations included; applications/examples excluded; non-editable external consumer |
| Dependency direction | Focused import checks reject core dependencies on service, concrete PostgreSQL or framework implementations; generic helpers have a neutral private home |
| Reviewed public surface | Inventory exports/protocols first; extend targeted signature/schema/behavior checks without freezing private helpers |
| Relevant compatibility evidence | Add dependency manifest/lock, adapter code and framework fixtures to lower/latest PR triggers; retain resolved inventories |
| Navigable design | Local links/anchors, unique canonical ADR IDs and referenced acceptance files |

Demonstrate that intentionally forbidden imports or extra completions fail the relevant guard.
Do not add a public API just to ease testing. Retain real-database/upgrade gates; if CI cost becomes
a problem, measure redundancy and propose an explicit matrix instead of silently dropping coverage.

**Exit:** deliberate architectural violations are caught and guards run on the changes they protect,
while core-only consumers remain lightweight.

## D. Choose one concrete host workload before expanding scope

Start with a host-owned scenario, which may be deterministic/offline. Specify concurrency/admission,
conversation length, input/tool payload sizes, downstream timeouts, side-effect idempotency,
restart/recovery expectations and privacy constraints. Do not infer capacity from test counts.

The strongest current design pressure is **history/context growth**: runtime loads the whole thread.
Measure it first. If needed, design context selection separately from durable history, preserving
tool-call/result pairs and system policy, keeping replay unchanged and making omissions observable.
No implicit deletion, truncation, summarization or semantic retrieval.

Credentialed provider canaries are an optional separate lane requiring an approved provider/model,
account, fixture, secret provisioning and spend cap. Offline architecture work is not blocked on them.

**Exit:** a reproducible host scenario demonstrates a gap, a small ADR compares alternatives, and
candidate-wheel acceptance verifies the chosen contract. Versioning follows the actual change.

## Deferred until justified

- Distributed workers, resumable workflows, human-approval queues or exactly-once tools need a
  separate execution/side-effect model, not merely longer run leases.
- RAG/pgvector, semantic memory and summarization need retrieval/context requirements and data policy.
- More providers/frameworks need a consumer and supported subset; do not invent a universal vendor
  schema or extend the descriptor's framework labels speculatively.
- Authentication/RLS, hosted control planes, scheduling, UI and multiple databases are separate
  product decisions, not prerequisites to using the library.
- Feedback outbox/evaluation/automatic optimization remain separate; immutable feedback does not
  imply exactly-once export or learning behavior.

## Definition of a finished iteration

Each implementation PR states the problem, owner, current-versus-proposed behavior, compatibility,
rollback implications and reproducer. Use venv and pytest. Relevant checks pass against source
and an installed wheel; persistence changes need real PostgreSQL. Update architecture/ADR/runbooks
when their claims change. Keep repository text English.

Promotion requires exact-head checks; tagging requires verified main and immutable artifact checks.
A metadata version or checked plan box is not completion. Python API, canonical contract and schema
changes each need compatibility review. Do not promise 1.0 until public-surface coverage and
independent-host evidence support it.
