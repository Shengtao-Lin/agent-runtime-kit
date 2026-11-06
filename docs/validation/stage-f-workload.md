# Stage F: Per-Model Input Boundary Workload

Status: requirements frozen before new Stage F measurement on 2026-11-06. This is an offline host
engineering requirement, not a provider token/context guarantee, hard memory cap or production SLO.

## Baseline and host requirement

Use actual published v0.7.0, wheel SHA-256
`17464f89c4516b9fc3ef706db54c7b69ea5de4d6dfc67049f650d449464b39cd`, in a non-editable external venv.
Release Desk at main `4b896d7708431e90013f482ec2a82777c671f730` is the independent host baseline.
Keep PostgreSQL schema `0002`, its explicit 65,536-byte initial window, incremental history reads,
deterministic routing and application-owned tools/resources. Preserve all Stage D/E artifacts.

For this stage, the host requires **every actual model dispatch** to stay within explicit canonical
input allowances, including calls after tools and trusted input hooks. The desired first behavior
is explicit rejection before dispatch, not silently dropping or rewriting messages. A rejected run
may have already executed tools or emitted provisional stream events; those effects are not undone.
The real release review is read-only. Synthetic effect counters separately exercise the same boundary
without writing to an external service or changing Release Desk business behavior.

## Counting contract and frozen acceptance

| Quantity | Definition and acceptance |
| --- | --- |
| Canonical messages | Complete ordered `Message.model_dump(mode="json")` list, all fields including IDs, compact JSON separators, `ensure_ascii=False`, UTF-8; at most 65,536 bytes at each protected dispatch |
| Canonical tool definitions | Complete ordered `ToolDefinition.model_dump(mode="json")` list with the same encoding; at most 8,192 bytes, checked independently of message allowance |
| Boundary | Equality passes; one byte over either allowance rejects before the wrapped/delegated model is called; empty lists count their JSON brackets |
| Preservation | No message truncation, implicit summarization, tool-result rewriting, current-input deletion, automatic retry or successful-history append for rejected runs |
| Protected success | Valid invoke/stream outputs, complete durable history, usage, terminal events and exact saved replay remain unchanged |
| Resource correctness | Closed delegated iterators on cancellation/abandonment/failure; zero remaining host tasks, pool checkouts and tagged database sessions after teardown |
| Runtime outcome | Safe nonretryable failure category and content-free diagnostics; executed tool effects remain observable; successful replay performs no new checks, model calls or tools |
| Dispatch-check overhead | For admitted 1 KiB, 16 KiB and near-64 KiB canonical message inputs with representative tools, added per-call p95 at most 10 ms on the fixed local host; compare three independent baseline/candidate timing processes, 500 calls per size after 50 warmups |
| Allocation diagnostic | Separate from timing: added checking allocation peak at most 4 MiB for the admitted-input profiles; record RSS without calling Python allocation a process bound |

The byte allowances are a deliberate synthetic host policy chosen before measurement. They do not
derive from a provider's context size. Tool descriptions/schemas can grow independently of messages;
neither allowance may be omitted from a claim that both are protected. The combined allowances are
not a serialized vendor request size. Provider mapping, wire envelopes, encoding overhead, token
counts, generation limits and provider context restrictions remain separate host/provider concerns.
No generic tokenizer or universal vendor encoder is part of this stage.

## Staged correctness scenarios

1. Observe the actual Release Desk composition through sixty consecutive reviews, in invoke and
   response-streaming modes. Record each underlying model call's complete message/tool byte counts,
   not only runtime entry. Preserve all 240 messages and exact replay; report a missing reproduction
   honestly rather than changing the case until it passes a preferred conclusion.
2. Use focused synthetic model/tool fixtures for a single 256 KiB tool result, several individually
   smaller tool results exceeding the allowance in aggregate, and multi-round growth. Bound rounds
   to three and tool calls to four per round. Keep the completed call/result relationship intact.
3. Exercise exact and one-byte-over boundaries, Unicode, escaping, large tool definitions, multiple
   current messages and host input-hook mutation after initial selection. Fail before the protected
   delegate; retain complete original request and historical data according to existing run semantics.
4. Exercise failure after a tool effect, streamed provisional output before rejection, successful
   replay after stricter configuration, failed-key behavior, cancellation, deadline and explicit
   stream closure. Prove error timing with delegate/tool counters and real durable records.
5. Verify native generate, native streaming and generate-only fallback. For real LangChain/LangGraph,
   place the host guard at the explicitly integrated actual model boundary after host mapping.
   Include a negative control showing that a guard around entry alone cannot protect internal calls.
   Do not claim interception of arbitrary framework-native models or framework-owned tool policy.

## Experiment safety and reproducibility

- Use deterministic offline models and local synthetic tools. No provider credentials, payments,
  private GitHub data or external writes. The existing optional public release metadata canary remains
  a separate adoption check, not model-quality evidence.
- Main conversation profiles have sixty historical turns and one active conversation. Focused growth
  fixtures have at most twelve tool results, each at most 256 KiB; all fixture input content together
  stays below 4 MiB. Do not combine the long conversation, largest tools and concurrency extremes.
- Host pool size two with no overflow; invocation deadline thirty seconds, cleanup/shutdown six
  seconds. Each external baseline/correctness process has a 180-second parent timeout; three-minute
  experiment safety is not responsiveness acceptance. Serialize latency benchmarks without competing
  local workloads. Use separate processes for allocation diagnostics.
- A sampled one-GiB RSS safety ceiling aborts workload/benchmark children; preserve failures. This
  sampler can miss brief spikes and does not make the application safe for arbitrary payload sizes.
- Freeze the validation lock and record Python/platform/PostgreSQL, actual wheel digest, package
  provenance, scenario settings and exact measured source hashes. Preserve raw call counts, results
  and failure reports. Never overwrite a completed manifest or silently relax the acceptance above.
- Use the dedicated `ark-stage-f-validation` test project. Stop only this project's services at
  completion; retain its volume and reports. Do not restart or delete Stage E evidence volumes.

## Decision and delivery

Compare unchanged behavior, host-owned `ModelClient` wrapping, native-loop integration and an
explicit reusable model-boundary policy. Prefer existing public extension points if they can satisfy
the complete contract. A native-only implementation cannot imply framework coverage. A universal
runtime guard cannot see arbitrary framework-internal requests. Trusted extensions may mutate their
own inputs; document the actual counting-to-dispatch boundary without claiming a security sandbox.

Record an ADR from the reproductions and a component runbook with enable/disable, overflow diagnosis,
tool-effect/stream implications and rollback. If a host-only integration suffices, validate it against
the unchanged actual wheel and do not force a library version bump. A public library behavior/API
change additionally requires compatibility review, candidate-wheel acceptance, actual publication
and independently locked host adoption. All outcomes require relevant CI, exact-head/main checks,
clean worktree and evidence-preserving service cleanup before goal completion.
