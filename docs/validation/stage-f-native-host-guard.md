# Stage F native host guard evidence

Status at the native acceptance revision: native candidate validated; framework integration and
final application adoption remained separate Stage F acceptance items. Their subsequent results are linked from
the [delivery audit](stage-f-delivery-audit.md). The library stays at actual
published v0.7.0. The host module is not exported from `agent_runtime` and is not
enabled by Release Desk composition at that measurement revision.

## Boundary and observed behavior

The application-owned `model_inputs.py` uses the public `ModelClient` and optional
`StreamingModelClient` contracts. It checks complete canonical messages and tool
definitions immediately before delegate generate or stream opening. A generate-only
delegate retains the existing native stream fallback. The wrapper closes delegates
that expose `aclose`; runtime deadlines/cancellation remain runtime-owned.

The five [growth cases](stage-f-input-growth.md) now reject before the oversized
delegate dispatch in invoke, stream and generate-only fallback. The multi-round
case admits three calls then rejects the fourth; the hook/schema cases admit zero.
Failures are nonretryable `host_model_input_exceeded` with a fixed public message
and only dimension/count/limit diagnostics. Canonical JSON encoding failures use
`host_model_input_invalid`. These are host-owned subclasses of `RuntimeKitError`.

Earlier tool effects and provisional stream events survive. No failed turn is
appended to successful history, durable runs have a safe error code and no response,
and reusing their failed idempotency keys performs no new work. Deadline,
cancellation and explicit stream closure settle runs and close iterators. Tests
assert no surviving host tasks, pool checkouts or tagged PostgreSQL sessions.

Protected and unprotected frozen Release Desk each complete sixty reviews per
invoke/stream mode, with 120 model calls and all 240 history messages. Exact saved
replay bypasses model/tool work and budget checks even after lowering both budgets
to two bytes. Complete canonical Unicode/escaping/multiple-message boundaries and
the exact frozen 65536/8192-byte limits admit equality and reject one byte over.
Normal result objects and token usage are preserved.

## Independent validation

The external runner verifies the actual published wheel SHA and all 60 installed
package files, records dependencies and exact sources, and uses PostgreSQL 16.15.
On Windows/Python 3.12.10:

- `stage-f-host-guard-1`: 44 passed, two failed plus two teardown errors. The invalid
  Unicode fixture was rejected during canonical construction before reaching the
  guard; escaped parameter IDs also contained a Windows path separator. The
  corrected test mutates a valid trusted host object after construction, and safety
  report filenames now hash the full node/process identity.
- `stage-f-host-guard-2`: 56 passed, including lifecycle and protected host cases.
- `stage-f-host-overhead-1`: 56 correctness tests passed, followed by all eight
  isolated measurement processes, six profiles per process.
- `stage-f-host-guard-3`: 64 passed in 14.35 seconds, adding the exact frozen limits.

All attempts are preserved in [raw evidence](evidence/stage-f-native-host-guard/raw.tar.gz)
with [SHA-256 checksums](evidence/stage-f-native-host-guard/checksums.json). Downloaded
wheels are excluded from the archive; their identities and installed-byte checks
are retained. The original pre-rebase performance source commit was `84486ff`;
the manifest's complete source hashes are the authoritative measured identities.

## Frozen overhead acceptance

Three independent matched baseline/candidate process pairs ran serially, each with
500 measured calls after 50 warmups per profile. Inputs were exactly 1024, 16384 and
65536 canonical message bytes, with a 223-byte representative tool definition list.
Generate and stream were both measured. Allocation used a separate process pair.

| Metric across all admitted profiles | Observed maximum | Frozen limit |
| --- | --- | --- |
| Added per-call p95 | 0.2068 ms | 10 ms |
| Added Python allocation peak | 131218 bytes | 4194304 bytes |

Raw timing samples, baseline/candidate p95s and added allocation diagnostics are
retained. RSS safety samplers passed; sampled RSS is not a hard process memory
bound. These observations cover this local deterministic host, not provider
latency, token limits, wire payload sizes or arbitrary oversized input allocation.
CI runs correctness; the local performance gate is not imposed on shared runners.

## Decision at this measurement revision

The existing public boundaries suffice for native protection in this evidence.
Before accepting a host-only design for all Stage F scope, validate explicitly
integrated real LangChain/LangGraph model calls after mapping, including the
entry-only negative control, then adopt and document the host configuration.
No guard can claim to intercept arbitrary framework internals or to sandbox a
trusted delegate that subsequently changes its own request.
