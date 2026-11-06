# Stage E Delivery Audit

This audit separates accepted implementation evidence from publication and host adoption.
Updated on 2026-11-06 after actual v0.7.0 publication, independent adoption acceptance and local
service cleanup. Final-head and applicable main checks gate promotion of this adoption revision.

## Requirement evidence

| Goal requirement | Evidence and scope | Status |
| --- | --- | --- |
| Frozen actual v0.6.0 baseline and host requirements | [Workload specification](stage-e-workload.md), frozen in `d587d1f`; checksum-pinned wheel, 64 KiB window, 100/1,000/10,000 turns, staged concurrency, safety ceilings distinct from acceptance budgets | Passed |
| Repeated matched measurement | [Accepted comparison](stage-e-incremental-history.md): three repetitions per target, 42 fresh processes and 510 continuations each; matching workload sources/dependencies; per-repetition absolute gates and pooled short-history gate | Passed |
| Measurement boundaries | Separate timing and diagnostic lanes, absolute/delta RSS, allocations, canonical conversion, driver JSON decoding, cursor opening and separate EXPLAIN; transport is not independently isolated and nested times are not added | Passed with documented measurement limits |
| Evidence-led design | [ADR 0016](../adr/0016-history-materialization-and-validation.md) compares unchanged behavior, host rotation, batching, incremental consumption, selective reads and metadata; rejected batching/ORM candidates and unchanged frozen thresholds remain archived | Passed |
| Complete semantic preservation | Differential selector acceptance, old system messages, current-boundary/cross-batch tools, oversized groups, old malformed/corrupt history, stored-record identity, complete durable transcript and exact replay | Passed; see implementation acceptance |
| Default and extension compatibility | Unchanged `get_messages` default; optional protocol and explicit opt-in; unsupported configurations rejected; custom-store/subclass boundaries documented | Passed |
| Real persistence and resource ownership | PostgreSQL cursor snapshot/concurrent append, early close, cancellation, timeout, same-thread contention, restart, zero retained connections/sessions; installed native and real LangChain/LangGraph consumers | Passed |
| Candidate release | [v0.7.0 readiness](v0.7.0-readiness.md): 758 source cases, 359 provider/native cases, 176 actual-v0.6.0 upgrade cases, shared-engine soak, core imports and incremental smoke | Passed |
| Release promotion | PRs #49/#50 passed exact final-head gates. A main rehearsal exposed a recovery-test deadline error; [correction evidence](stage-e-recovery-probe.md) and PR #51 preserve the failure. Corrected final-head/main checks and tag verification/publishing all passed | Passed |
| Actual assets and independent adoption | [Publication evidence](v0.7.0-publication.md): downloaded wheel/sdist digests, all sixty package files matching accepted code, GHCR identity/non-root imports, actual-wheel fresh/upgrade/soak/smoke acceptance, independent published lock and fourteen app tests plus public restart/replay/feedback canary | Passed |
| Local service cleanup | Owned `ark-stage-e-validation-postgres-1` stopped; `ark-stage-e-validation_agent-runtime-postgres` volume and all raw reports retained; no other project stopped | Passed |
| Adoption revision promotion | Exact final-head CI/application checks before merge, applicable resulting-main checks, and clean final worktree | Required promotion gate |

## Guarantees that must remain separate

- Database work and transfer still scan every historical row. No selective-read or constant-time
  guarantee follows from lower Python materialization.
- A 256-row cursor batch is not a byte bound. Large individual payloads, mandatory messages and
  pending tool relationships remain workload-dependent.
- The configured context window limits initial canonical selector output. It does not cap later
  model inputs, provider tokens, framework checkpoints or trusted hook mutations.
- Measured RSS and latency establish the frozen synthetic host acceptance, not a production SLO
  or universal process-memory limit. Diagnostic timing is not responsiveness acceptance.
- Successful replay performs no new history read, model call or tool work. Failed/blocked runs and
  external tool side effects retain their existing contracts.

## Delivery sequencing

Release-preparation main was verified before tagging. Actual immutable artifacts were verified
before pinning the independent application. Candidate and actual published acceptance retain separate
reports and hashes. Promotion of this final adoption revision still requires exact-head/main checks;
verify their authoritative GitHub run/PR state and the final clean worktree before marking the goal
complete. Local cleanup evidence is retained in `build/stage-e-cleanup.json`.
