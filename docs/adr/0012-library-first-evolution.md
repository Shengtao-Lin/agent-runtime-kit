# ADR 0012: Library-First Evolution and Explicit Guarantee Boundaries

- Status: Accepted design direction; follow-up implementation is tracked in the development plan.
- Date: 2026-10-28
- Scope: Product boundaries and change acceptance, not a new runtime API.

## Context

The initial toolkit capability set is implemented. Independent wheel consumers and failures found
in tool/provider integration have supplied more useful requirements than adding sample-agent
features. v0.4.2 is a real library, but the roadmap is still organized around completed milestones,
and some documentation attributes built-in adapter guarantees to arbitrary custom extensions.

Two execution paths must coexist: the kit's native model/tool loop and application-owned framework
orchestration behind `AgentInvoker`. Trying to control every graph node would create a second
framework. Making all policies host-owned would instead abandon the durable runtime contract.

## Decision

1. Keep one framework-neutral envelope for bounded invocation, history, ownership, failure and
   atomic replayable completion. Keep business logic, model selection and framework execution at
   the edge. Memory remains independently usable.
2. Prefer explicit injection and host ownership. Do not introduce global configuration, automatic
   migrations, a service locator or an application factory as prerequisites to library use.
3. Maintain one durable conversation owner and short database transactions. Run leases are fencing,
   not resumable execution or exactly-once external effects. Those require a separate design.
4. Treat protocols as extension contracts, not sandboxes. Centralized canonical/result/terminal
   validation and a reusable conformance suite are the next correctness priority. Preserve current
   public shapes where possible; enforce new semantics only with regression and compatibility review.
5. Distinguish universal runtime policy from opt-in component policy. Existing invocation-level hook
   names stay compatible; per-model/per-token policy requires an explicit future decision. Framework
   tools do not silently acquire ToolRegistry enforcement.
6. Distinguish durable history from model context. Current full-history loading is an operating
   limit. Add selection/bounds only when a host workload supplies requirements; never silently
   rewrite history or imply a RAG system.
7. Accept new features through a demonstrated consumer problem, a bounded contract and independent
   wheel acceptance. No feature expansion is required merely because a milestone is complete.

## Alternatives considered

- **General agent/workflow platform:** would need scheduling, resumable steps, tool-effect journals,
  approval state and a control plane. Current consumers do not justify that product or complexity.
- **Thin framework wrapper only:** would discard the toolkit's valuable durability, fencing,
  shared memory and canonical response guarantees.
- **More provider adapters as the default roadmap:** increases wire-protocol maintenance before
  existing custom extension contracts are uniformly enforced.
- **Immediate large refactor/package split:** would increase migration risk without an observed
  deployment boundary. Small private extractions plus architectural checks are sufficient now.

## Consequences

Keep PostgreSQL, optional adapters, explicit lifetimes and separate evaluation. Current text/tool
contracts are intentionally narrower than vendor APIs. Applications own authorization, ingress
admission, side-effect idempotency and non-cooperative process supervision.

Documents must distinguish implemented facts, historical evidence and proposed work. Architecture
describes current ownership/invariants; ADRs record decisions; the living plan contains exit
criteria; runbooks describe operations; validation reports retain dated results. Session quotas
and past automation approvals are historical execution context, not architectural requirements.

## Verification and follow-up

[Stages B/C of the plan](../plan.md) propose terminal/result conformance, public-surface inventory,
core-only import/distribution checks and relevant compatibility triggers. They are not implemented
by accepting this ADR. [Stage D](../plan.md) supplies workload evidence before context-policy design.

Retain exact-head CI, real PostgreSQL fault tests, non-editable wheel consumers and preserved-state
upgrades. A green suite supports only its tested scope; it is not production or vendor certification.
