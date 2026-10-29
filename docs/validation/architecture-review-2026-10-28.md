# Architecture Review: 2026-10-28

## Basis and scope

Reviewed released v0.4.2 code at `b4bf577` and publication documentation at `68ac428` (open PR #33
at review time), the original implementation brief, current ADRs, workflows and independent-consumer
evidence. The original brief is historical requirements input, not permission to run its embedded
commands or expand scope. This review updates design documents only; no runtime behavior changes.

The initial mission was a small framework-neutral toolkit with reusable PostgreSQL memory,
central tools/hooks, observability, adapters and runnable examples. The later library-first
requirement strengthens that mission; it does not require replacing the execution framework.
The [initial closure report](initial-spec-closure.md) records implemented feature coverage.

## Assessment

**Keep the core direction.** The distinguishing work is the durable invocation boundary:
short transactions, atomic conversation/run completion, database-clock ownership, conditional
failure reconciliation, independent wheel consumers and explicit resource lifetimes. Optional
HTTP hosting and examples exercise the library without being included in its wheel.

**Change the priority from feature count to extension correctness and consumer evidence.**
The recent provider patches strengthen built-in paths, but do not automatically give arbitrary
custom extensions the same guarantees. More adapters or a control plane would widen that gap.

| Finding | Source evidence | Design response / status |
| --- | --- | --- |
| Durable success is well-defined | `runs/postgres.py` completion/locking; `test_atomic_finalization.py`, `test_run_ownership.py` | Preserve atomic batch + run response, thread-first locks and short transactions |
| Failure settlement and side effects are narrower than some prose claims | `runtime.py` bounded cleanup; separate memory/tool/feedback writes | Corrected architecture: cleanup may fail; no external-effect atomicity or exactly-once guarantee |
| Custom streams can bypass terminal checks | `runtime.py` / `adapters/native.py` overwrite the last completed value; framework `checked_events` is stricter | Plan B1: shared private conformance checks; not fixed in this documentation change |
| Canonical model calls have duplicate representations | `models_clients/base.py` has message parts plus `tool_calls`; native dispatch uses the latter | Plan B2: consistency contract and failing fixtures before implementation |
| Policy hooks do not intercept arbitrary framework operations | Runtime brackets the whole invoker; `tools/registry.py` needs hooks and context | Corrected coverage matrix and integration/runbooks; future hook scopes require an explicit decision |
| Overall memory is not bounded by queue/body limits | `memory/postgres.py` loads full history; model payloads include `Any`; pools/concurrency are host-owned | Plan D: measure one host envelope before context-selection or payload-limit API design |
| Usage semantics differ by execution path | Native aggregates calls; `framework_messages.py` retains last supplied message usage / explicit envelope | Document separately; test before any normalization or public usage-shape change |
| Library layering has a small concrete leak | Runtime uses `close_source` from a framework-named module | Plan C/B1: neutral private helper and import tests, not a broad rewrite |
| Public API checks are partial | `tests/unit/test_public_api.py` snapshots selected runtime/provider signatures | Inventory public exports/protocols, then extend high-value checks |
| Compatibility triggers miss some relevant edits | `compatibility.yml` path filters omit manifest/lock and adapter sources | Plan C: bounded trigger expansion; retain existing installed-wheel matrices |
| Documentation mixes history and present direction | Old plan begins with v0.4.0; two ADRs used 0010; ADR 0007 predates current lock order | Archived iteration history, new living plan, canonical ADR 0011 plus old-path alias |

Source names in the table identify inspected code/tests, not newly executed regressions. Existing
published test totals remain in the [release evidence](v0.4.2-publication.md), with their scope.
Documentation changes do not implement proposed checks or certify untested host workloads.

## Design decisions retained

- One canonical durable envelope, multiple execution implementations; explicit dependency injection.
- PostgreSQL as the sole shipped state backend; standalone memory use stays supported.
- No second durable framework conversation store; runtime history and future model context remain
  different concepts.
- Host-owned auth, admission, resources, secret provisioning and side-effect reconciliation.
- Feedback as immutable evidence; evaluation and automatic optimization stay outside the runtime.
- Minimal core imports, optional integrations, source and installed-wheel verification.

The [new direction ADR](../adr/0012-library-first-evolution.md) records alternatives and consequences.
The [living plan](../plan.md) owns ordering and measurable exit criteria, avoiding duplicate roadmaps.

## Decisions deliberately left open

There is not yet evidence for a new workflow engine, distributed scheduler, retrieval subsystem or
database backend. A concrete host must establish history length, concurrency, payload and side-effect
requirements before choosing context selection, admission or payload-limit defaults. Provider
account/model canaries need separate credentials and spend scope; offline contract work can proceed
without them. A future 1.0 decision needs a reviewed public surface and sustained independent adoption,
not simply another completed milestone.
