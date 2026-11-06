# Stage E Release Recovery Probe Correction

The first release-preparation main rehearsal at `ca988069f09e310b22d8beca78a9a66eb3781a77`
failed in [release consumer job 127772844834](https://github.com/Shengtao-Lin/agent-runtime-kit/actions/runs/42181695962/job/127772844834).
The actual-v0.1.1 upgrade lane passed 129 tests and failed the exhausted-pool recovery probe.
No tag or publication followed this failed gate.

The injected pool exhaustion correctly timed out without a model call. After releasing all held
connections, readiness and absence of a claimed run both passed. The subsequent normal invocation
then exceeded the same 200 ms deadline used to inject the fault. That deadline was inadvertently
also a model/transaction performance requirement; this test is intended to verify pool recovery.

Keep the 200 ms deadline, outer three-second guard, no-model-work and no-claim assertions for the
fault phase. For recovery, compose a normal-budget runtime with the **same engine and pool**, same
agent identity, same idempotency key and, for the analogous lock probe, the same thread. Inject a
250 ms model delay to make the distinction deterministic. Verify exactly one recovery model call,
durable success and zero checked-out connections afterward. The original fault model remains unused.
Both pool-exhaustion and row-lock probes use this boundary.

This is a consumer-test correction. It changes no library code, published artifact, default runtime
deadline, Stage E workload, frozen responsiveness/memory budget or measured comparison. It proves
pool reuse by a newly composed runtime; it does not claim a 200 ms end-to-end recovery SLO.
The failed workflow and its uploaded evidence remain available. The corrected revision requires
its own final-head and main verification before tagging.
Release rehearsal now also triggers on consumer harness/script changes, so this validation boundary
is exercised directly before and after merge rather than waiting for a later version bump.

Local validation used an external Windows Python 3.12.10 venv, the unchanged v0.7.0 candidate wheel
and a new real PostgreSQL database. Actual checksum-pinned v0.1.1 records survived the upgrade:
130 pytest cases passed, four historical readiness probes were deselected, and none were skipped.
Ruff, strict Pyright and documentation checks passed. Retain the [consumer evidence](evidence/stage-e-recovery-probe/raw.tar.gz)
and [checksum](evidence/stage-e-recovery-probe/checksums.json) separately from the original failed CI run.
