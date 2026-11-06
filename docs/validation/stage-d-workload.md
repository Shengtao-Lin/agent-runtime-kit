# Stage D: Long-Conversation Release Desk Workload

Status: workload specification and [three-repeat published baseline](stage-d-baseline.md) completed.
Candidate design and implementation remain in progress. This document does not
claim that context selection or bounded history retrieval has been implemented.

## Host scenario and requirements

A host reviews synthetic Python release metadata repeatedly on the same conversation thread using
Release Desk's real `ReviewRouter`, input models and review checklist. Its deterministic routing
needs the latest review request/tool result, while the durable transcript remains available for
audit. No network/provider account is needed. Tool activity is read-only; successful retry uses the
same semantic request/key, failed keys never trigger automatic reexecution.

The experimental initial-context allowance is 65,536 bytes of compact UTF-8 canonical message JSON,
including array punctuation and all serialized message fields. This is a host operating requirement,
not a vendor token limit. Model-call inputs are measured separately: a native tool round can grow
after invoker entry. No library or framework-wide per-model limit is implied.

## Staged matrix

| Axis | Values | Other dimensions |
| --- | --- | --- |
| History | 10, 100, 1,000 complete review turns | One conversation; 1 KiB tool-result padding |
| Concurrency | 1, 4, 16 independent conversations | Ten turns; 1 KiB padding |
| Result size | 1, 16, 256 KiB padding | Ten turns; one conversation |

Padding is a deterministic extra field in the canonical tool-result JSON; the real review checklist
and routing still execute. Reports distinguish padding size from the total serialized result size.
Do not combine every maximum. A public-API fixture seeds complete historical transcripts in bounded
batches; measured continuations execute real native model/tool loops. Seeding is excluded from
latency/allocation measurements and is not described as 1,000 completed runtime invocations.
A small control executes successive real turns to validate fixture shape and continuation semantics.
History values describe the seeded starting point: every successful measured continuation adds four
messages. Reports retain the effective message count for each sample; comparisons must match those
counts instead of describing all samples as a fixed-length history.

Each invocation has a 30-second runtime deadline; the host admission gate allows four concurrent
operations with a 30-second queue timeout. Pool size is two with no overflow and five-second pool
timeout. Requests arrive as a finite batch (at least twenty per profile, at least two per independent
conversation; smoke uses two per conversation), with same-thread
continuations sequential. Same-thread conflict, cancellation, slow consumers and downstream failures
are separate correctness scenarios, not mixed into successful latency percentiles.
The slow-stream profile adds 20 ms per text event to the real review output; it does not claim to
fill the 64-event queue. Existing larger-stream backpressure acceptance remains a separate gate.

An experiment process has a 180-second wall-clock limit and a sampled 512 MiB RSS abort limit.
These are experiment guardrails, not production SLOs or hard OS-enforced memory reservations.
Limit hits must be recorded as failed/bounded experiments; never silently reduce the matrix or drop
the case from a comparison. Diagnose and choose a bounded rerun while preserving the original result.

## Metrics and controls

Record raw samples, not just aggregates: host admission wait, admitted service and end-to-end time;
first text delta measured from submission and admission; history-read time/row count/canonical JSON
bytes; initial invoker context bytes; each model-call input byte count; Python traced allocations;
sampled process RSS; pool/task/session cleanup. Canonical JSON bytes are not PostgreSQL wire bytes
or vendor prompt tokens. History timing includes pool acquisition, SQL execution and decoding;
metric serialization time is recorded separately and contributes to measured invocation latency.

RSS sampling uses the workload-only [psutil process API](https://psutil.io/) and includes the process,
dependencies and instrumentation; Python allocation tracing does not include all native allocations.
Seed/warmup RSS and measured RSS must be distinguished. Sampling can miss short peaks. The sampler
also observes RSS during synchronous work from a separate host thread. It adds measurable overhead.
Bound metric storage and avoid retaining messages in recording models.

Run at least three fresh-process repetitions for baseline and candidate on the same host, database
configuration, fixture definitions and locked dependencies. Preserve installed package provenance,
actual wheel SHA-256, dependency inventory, host/Python/clock data and workload source hashes.
Paired comparisons must reject mismatched profiles; no arbitrary p95/RSS CI regression threshold
is introduced before repeated stable-runner measurements establish expected variation.

## Decision and delivery gates

Measure actual published v0.5.0 first. Distinguish initial-context growth, database materialization,
native per-model growth and host admission costs. Compare host-only selection, opt-in runtime
selection, optional bounded store reads and no change in an ADR before introducing a public API.
If a claimed resource bound requires bounded reads, trimming after full history loading is insufficient.

Any solution preserves durable history, protected system policy, the current request and complete
tool-call/result relationships; insufficient mandatory budget fails explicitly. Default behavior,
hooks, exact replay, durable failure/ownership and framework-owned execution need explicit tests.
Small smoke/correctness checks belong in CI; full repeated measurements remain reproducible artifacts.
Candidate-wheel and actual-release/application adoption are required when library behavior changes.

Do not add RAG, summarization, distributed workers, additional providers or paid canaries here.
