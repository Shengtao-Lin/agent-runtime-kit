# Development Plan

Reviewed on 2026-10-28 against v0.4.2; execution status updated on 2026-11-06 for v0.5.0.
This is the living implementation queue, not a release log
or authorization to spend money, select credentials or change production systems. Historical
execution records are [archived separately](validation/iteration-history-through-v0.4.2.md).

## Direction and baseline

Keep a small importable runtime library that makes one application-owned agent invocation durable,
observable and explicit about failure. Preserve framework-owned execution, one conversation owner,
optional integrations and host-owned resources. See [architecture](architecture.md),
[ADR 0012](adr/0012-library-first-evolution.md) and the
[design review](validation/architecture-review-2026-10-28.md).

- Initial capability scope is implemented; see [initial-spec closure](validation/initial-spec-closure.md).
- v0.5.0 is published with the B/C extension and architectural contracts below, on top of v0.4.2's
  atomic/fenced persistence, lifetime management and usage accounting. Contract is `v1`, schema `0002`.
- The 2026-10-28 review used release commit `b4bf577` and publication documentation at `68ac428`.
  [Publication PR #33](https://github.com/Shengtao-Lin/agent-runtime-kit/pull/33) subsequently passed
  CI and merged on 2026-10-29. This revision preserves that review's evidence and current priorities.
- Release Desk now pins actual v0.5.0 in its independent lock; local published-wheel application
  tests and the public metadata/restart/feedback canary passed. See
  [publication and adoption evidence](validation/v0.5.0-publication.md). PR #35's earlier v0.4.2
  adoption remains historical evidence.
- Existing gates cover real PostgreSQL, non-editable wheels, preserved-state upgrades, container
  shutdown and selected dependency matrices. They do not prove universal extension conformance,
  production load capacity or account-specific provider compatibility.

## Priority order

| Stage | Outcome | Depends on |
| --- | --- | --- |
| A. Close release adoption | Completed for v0.4.2; repeated for actual v0.5.0 below | Verified assets and application acceptance |
| B. Enforce extension contracts | Shipped in v0.5.0 | Terminal/canonical/policy acceptance |
| C. Guard library boundaries | Shipped in v0.5.0 | Import, packaging, API, documentation and compatibility guards |
| D. Validate one host workload | Next design phase; not part of v0.5.0 | A named host scenario and operating envelope |

A and B can be prepared independently; keep separate PRs and acceptance evidence. Documentation
clarification does not implement B/C/D. Do not promise a version or feature release before its
compatibility impact and acceptance criteria are known.

## A. Complete actual-release adoption

Completed on 2026-10-29 in PR #35: the published wheel's pinned URL/digest, eight application tests
and public metadata/restart/history/feedback acceptance passed in CI with real PostgreSQL.
Local Docker was unavailable; these are CI results, not a local-database claim.

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

Implemented in development source on 2026-10-29: shared private terminal/closure handling, seven
reproduced regression failures, custom-model unit fixtures and PostgreSQL failure/replay tests.
PR #36 passed CI and the standalone application matrix, then merged; main CI also passed.
This behavior shipped in v0.5.0 and is absent from v0.4.2.

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

Implemented in development source on 2026-10-29 and merged in PR #37 after complete PR checks.
Main CI and the 2026-11-02 lower/latest dependency matrix passed at `bc3f59e`.
[ADR 0014](adr/0014-model-result-and-tool-payload-contracts.md) records the retained fields, native
snapshot boundary, finite JSON payload rules, error timing and compatibility implications.
The initial public-model reproducer had 48 failing cases. No version/tag or schema change is made.
B2 behavior shipped in v0.5.0 and is absent from v0.4.2.

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

Implemented as acceptance coverage on 2026-11-05. Native and real framework consumers exercise
phase ordering, explicit registry composition, blocked/failed outcomes, cancellation and replay;
framework-native tool bypass is tested explicitly. See
[the policy evidence](validation/policy-coverage-2026-11-05.md). PR #38 merged after CI and
lower/latest compatibility checks passed; main CI passed at `4f3a7e8`.

Keep existing hook names compatible while documenting invocation-level semantics. Test deliberate
runtime/tool hook composition; framework-native operations bypass the registry unless integrated.
A per-model/per-token hook is a separate decision, not a silent reinterpretation of existing names.

**Exit:** tests/runbooks show what is enforced and where, including provisional output before an
output guardrail. Do not claim a universal framework safety interceptor.

## C. Add architectural fitness checks

Import, dependency-direction and wheel-content guards merged in PR #39 after CI and independent
application checks passed, including an external core-only wheel probe. The probe reproduced eager adapter loading;
adapter exports now resolve implementations lazily while preserving their public import paths.
See [boundary evidence](validation/library-boundaries-2026-11-05.md). Documentation guards now
check linked local evidence, heading anchors and canonical ADR identity; CI and release verification
run the guards, with negative fixtures for broken links/anchors and duplicate decisions.
PR #40 merged after final-commit CI and release rehearsal passed. PR #41 then merged after CI and
the Python 3.11/3.12 lower/latest dependency matrix passed. Its [public API inventory](public-api.md),
focused export/protocol checks and expanded compatibility triggers complete Stage C's implementation.
The target-wheel consumer runs those checks outside the checkout. Release promotion remains separate.

Local public-surface evidence: 575 unit tests passed (72 integration cases deselected), Ruff and
strict Pyright passed, and the external candidate-wheel consumer passed 75 cases with one
upgrade-only skip and four readiness probes deselected. This fresh-consumer result is not a
preserved-state upgrade claim. The report and dependency/provenance inventories are retained under
`build/public-api-consumer`; final-commit compatibility CI remains required.

| Invariant | Automated evidence |
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

Execution started on 2026-11-05 with the [Release Desk workload specification](validation/stage-d-workload.md).
The actual v0.5.0 wheel is the frozen baseline. Establish the staged history/concurrency/payload
measurements before selecting a context or storage API. The [completed baseline](validation/stage-d-baseline.md)
contains 45 processes and 972 samples; candidate design and implementation remain in progress.
PR #44 merged after final-head CI passed, including actual-baseline and candidate-wheel workload
smokes. [ADR 0015](adr/0015-opt-in-initial-context-window.md) chooses an optional initial-context
window. Its [implementation and independent comparison](validation/stage-d-context-window.md) passed:
all 972 candidate inputs fit, with full durable history preserved; 681 root tests and 111 installed
consumer cases passed. Final-head promotion and the resulting minor release/adoption remain pending;
full history materialization is explicitly outside the new byte guarantee.
The full Stage D goal includes any justified fix, independent acceptance and publication
when library behavior changes, not just the measurement report.

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

## v0.5.0 delivery scope (completed implementation and publication)

B3 and C shipped through focused PRs followed by an immutable v0.5.0 tag and verified actual assets.
Stage D workload/context design remains separate. The original delivery sequence was:

1. Verify runtime/tool policy composition, framework boundaries and durable blocked/failed replay.
2. Add automated import, distribution, dependency-direction and documentation fitness checks.
3. Inventory the supported public surface, protect important contracts and expand compatibility triggers.
4. Add checksum-pinned actual v0.4.2 preserved-state upgrades while retaining earlier source paths;
   prepare v0.5.0 migration/release documentation and run all candidate checks.
5. Publish verified artifacts, validate their actual bytes from an independent venv, then update
   Release Desk's independent lock/provenance and verify published-version adoption.

Evidence belongs with each PR and the final publication report. Completion requires merged changes,
passing final-commit checks and main/release verification, actual wheel installation/upgrades and
successful independent application adoption. Repository text remains English; tests use venv/pytest.

v0.5.0 has migration/release notes and a checksum-pinned actual v0.4.2 upgrade lane in CI and Release,
retaining every earlier source path. Final release-PR/main/tag checks passed, actual downloaded
wheel acceptance passed, and Release Desk's independent lock adoption passed local installation,
restart/replay and feedback checks. See [candidate readiness](validation/v0.5.0-readiness.md) and
[actual publication/adoption](validation/v0.5.0-publication.md). Final-commit CI/application checks
gate promotion of the adoption revision, as for every implementation change.
