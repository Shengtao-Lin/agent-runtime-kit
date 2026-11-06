# Stage F Delivery Audit

Stage F delivers an independently validated host policy using existing public model
extension points. No library API, version, dependency or schema change is needed.
This audit separates measured acceptance from final GitHub promotion gates.

## Requirement evidence

| Requirement | Evidence and scope | Status |
| --- | --- | --- |
| Freeze requirements before measurement | [Workload](stage-f-workload.md), original commit `51c3719`; canonical message/tool byte limits distinct from tokens, wire sizes, overhead and safety ceilings | Passed |
| Actual baseline and honest reproduction | SHA-verified actual v0.7.0, all 60 installed package files verified; [normal host](stage-f-release-desk-baseline.md) has no overflow; [five focused cases](stage-f-input-growth.md) reproduce post-selection growth | Passed |
| Minimal evidence-led design | [ADR 0017](../adr/0017-host-owned-model-input-budgets.md) compares unchanged behavior, entry checks, native integration, universal interception and explicit host wrapping | Passed; no new library interface |
| Every protected native dispatch | Generate, stream and generate-only fallback check messages and definitions before delegate dispatch; exact/one-byte-over limits, Unicode, hooks and accumulated tools | Passed; [native report](stage-f-native-host-guard.md) |
| Explicit framework boundaries | Real Runnable and compiled graph, final-mapper growth, internal tool rounds, protected normal/failure paths and entry-only negative controls | Passed; [framework report](stage-f-framework-boundaries.md) |
| Failure, effects and replay | Safe nonretryable host errors, no failed-turn success history, retained tool effects/provisional output, failed-key no-work behavior, exact successful replay under stricter budgets | Passed |
| Lifecycle and resources | Cancellation, deadline, explicit consumer close, balanced delegate streams, no retained host tasks/pool checkouts/tagged sessions; sampled RSS ceilings | Passed within documented cooperative/sampled limits |
| Frozen overhead acceptance | Three independent matched timing pairs, 500 observations after 50 warmups for 1/16/64 KiB generate/stream profiles; separate allocation pair | Passed: max added p95 0.2068 ms; added allocation peak 131218 bytes |
| Independent host adoption | Actual v0.7.0 independent lock, 23 application tests, every dispatch observed through sixty reviews, disable behavior, public read-only metadata canary and cross-process stricter-limit replay | Passed; [adoption report](stage-f-host-adoption.md) |
| Default compatibility and lightweight library | No changes to `src`, root manifests/lock or independent app lock relative to Stage E; existing API/import/build/upgrade/container gates retained | No library behavior change; final-head CI remains required |
| Operational documentation | [Runbook](../runbooks/model-input-budgets.md), [adoption/rollback guide](../adopting-model-input-budgets.md), public API, architecture and application settings documented | Passed |
| Evidence identity | Six raw archives checksum-verified; adopted policy bytes exactly match the frozen overhead manifest | Passed |
| Owned service cleanup | Only `ark-stage-f-validation-postgres-1` stopped; its volume and raw reports retained | Passed; [cleanup](evidence/stage-f-delivery/cleanup.json) |
| Promotion | Exact final-head checks before each merge, applicable resulting-main checks, final clean main worktree | Final promotion gate; verify authoritative GitHub state |

## Delivery sequence

- PR #53 froze requirements, actual host source and baseline CI.
- PR #54 reproduced focused unprotected growth and preserved both fixture attempts.
- PR #55 added the host module and native correctness/overhead evidence.
- PR #56 validated explicit real-framework boundaries and accepted the design.
- PR #57 adopts the policy in the separately installed application and adds operator guidance.
- The final audit revision closes documentation and evidence bookkeeping after adoption checks.

Each implementation PR is merged only after its exact head's applicable checks pass.
The final audit revision also requires CI before merge and applicable main checks
afterward; this document does not substitute for those authoritative run states.
No immutable library tag or asset publication is part of a host-only delivery.

The adoption head `54cc1f2777eff2570b258b0950fd0d930af234d2` passed
[CI](https://github.com/Shengtao-Lin/agent-runtime-kit/actions/runs/42250459786) and
[the application matrix](https://github.com/Shengtao-Lin/agent-runtime-kit/actions/runs/42250459796)
before [PR #57](https://github.com/Shengtao-Lin/agent-runtime-kit/pull/57) merged as
`fa52cffd4c6511b90bf158b74d1c401fd0b818b3`. The preceding framework main
`6b8eadf404923ab4fae54116b1e408acee555ff5` also passed
[its CI](https://github.com/Shengtao-Lin/agent-runtime-kit/actions/runs/42250380608).
Resulting adoption-main and final-audit-main checks are verified through GitHub
before goal completion; final audit promotion does not modify measured code.

## Preserved limitations

The check bounds complete canonical lists only. It is not a provider token/wire limit,
production latency SLO or memory sandbox. The delegate and host mappings remain trusted;
requests mutated inside a delegate are outside the counted boundary. Framework coverage
is explicit, not interception of arbitrary internal models or automatic ownership of
framework-native tool policy.

Earlier external tool effects and provisional output are not rolled back. Synchronous
serialization can allocate input-dependent memory, and sampled RSS may miss spikes.
The accepted profiles stay below the one-GiB sampled abort ceiling; the largest observed
accepted-process RSS is 137809920 bytes in framework validation. See the
[resource summary](evidence/stage-f-delivery/resources.json), with
[checksums](evidence/stage-f-delivery/checksums.json). These diagnostics do not alter the
predeclared four-MiB incremental allocation and ten-ms added-p95 acceptance criteria.

Initial context selection and incremental history remain separate. Complete history
is retained, database reads still scan all history, and successful replay bypasses new
checking/model/tool work. RAG, automatic summary, new providers, distributed execution,
token estimation and hosted control planes remain outside Stage F.
