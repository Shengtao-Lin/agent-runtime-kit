# Incremental History Reading Runbook

## Purpose and availability

Use this capability when complete transcript materialization exceeds a measured host resource
budget while the application still needs one continuous durable conversation. It is implemented
in development for Stage E and is **not available in the published v0.6.0 wheel**.
[Repeated independent acceptance](../validation/stage-e-incremental-history.md) passed; publication
is pending. See [ADR 0016](../adr/0016-history-materialization-and-validation.md)
and the [frozen workload requirements](../validation/stage-e-workload.md).

The host owns admission, engine/pool lifecycle, input/tool payload limits, timeouts and business
acceptance of omitted context. The library owns complete history validation, ordered selection and
closing the history iterator before hooks/agent execution. This is not retention, deletion,
summarization, retrieval or a per-model token budget.

## Prerequisites and configuration

Install a wheel providing the capability in a venv, keep PostgreSQL migration revision `0002`,
and supply memory and run stores in the same persistence domain. Review custom store overrides
before enabling a new read entry point. Construct the runtime explicitly:

```python
from agent_runtime import AgentRuntime
from agent_runtime.context import ContextWindow

runtime = AgentRuntime(
    agents=agents,
    memory=memory,
    runs=runs,
    namespace="release-desk",
    context_window=ContextWindow(max_bytes=65_536),
    stream_history=True,
)
```

The byte allowance is the synthetic Release Desk requirement, not a universal default. The
`stream_history` default is `False`; it must be a boolean. Enabling it without a window raises
`ValueError`, and a store lacking `StreamingMemoryStore` raises `TypeError` during construction.
There is no implicit fallback or environment-variable discovery by the runtime. Hosts may provide
their own configuration surface. Response streaming is independent of history streaming: both
`invoke` and `stream` use the selected history mode.

Existing `MemoryStore` implementations need no changes when the flag is false. An implementation
opting in must return an async generator from `stream_messages(thread_id, namespace=...)`, yielding
complete canonical `StoredMessage` records in deterministic sequence order from one read snapshot.
It must release owned resources on exhaustion, failure, cancellation and `aclose()`. The protocol
check detects structural availability, not correctness. A subclass overriding only `get_messages`
does not automatically apply that override to `stream_messages`; review filters/policies explicitly.

## Standalone consumption and ownership

For direct memory use, close the generator even when abandoning iteration:

```python
from contextlib import aclosing

async with aclosing(memory.stream_messages(thread_id, namespace="release-desk")) as records:
    async for stored in records:
        await consume(stored)
```

Do not accumulate all records into a list and expect a memory saving. Slow consumers retain a cursor,
transaction snapshot and pool connection until completion/close. Size admission and pool timeouts
for that ownership. Runtime consumption closes the cursor before hooks, provider/tool work and
durable writes; the host still closes its engine after runtime shutdown. Do not call store `close()`
per read: that disposes the shared engine.

For a custom canonical-message source, `await window.select_stream(source, current)` fully consumes
history and closes the acquired iterator when it supports `aclose`. It returns the same detached
selection and count summary as `select`. It cannot release hidden resources in an incorrectly
implemented custom iterator. Keep input objects stable while selection consumes them.

## Guarantees and limits

| Layer | Guarantee | Remaining responsibility/cost |
| --- | --- | --- |
| PostgreSQL history query | One ordered SELECT snapshot, 256-row fetch batches | Every historical row is scanned/transferred; server sort/spill and snapshot lifetime still matter |
| Canonical conversion | All returned records validated | A single large JSON payload can dominate allocation and decoding |
| Selection | Whole-group suffix, original order, all system/current content, complete tool relationships | Pending tool IDs and one payload serialization are not universally byte-bounded |
| Initial canonical input | Selected JSON bytes fit the explicit window or invocation fails | Hooks, mappers, tools and later model inputs can grow beyond it |
| Persistence/replay | Complete original transcript retained; exact saved replay avoids execution | Host retains responsibility for storage growth, backup and business side effects |

The selector checks omitted old exchanges too. Canonical read errors retain precedence over
selection errors discovered earlier in history. Current/system overflow never triggers automatic
truncation. Rows appended after the PostgreSQL cursor opens are absent from that snapshot; this is
not permission for another writer to mutate a live runtime conversation. Normal thread ownership
continues to serialize runtime writers.

## Observation and troubleshooting

Use `agent_runtime.context` hook attributes and `agent.context.*` telemetry for input/selection byte
counts and omitted groups, as described in [context selection](context-window.md). Memory spans use
operation `stream_messages`. These counts are not RSS, wire traffic or billing measurements.

| Symptom | Inspect | Action |
| --- | --- | --- |
| Configuration rejected | Window presence, boolean flag, store capability | Upgrade/implement the intended capability or explicitly disable it; do not silently fall back |
| History still slow | Total rows, query plan, decode and selection costs | Measure separate timing/diagnostic lanes; incremental retention still performs a complete scan |
| RSS spike despite a small window | Largest stored payload, fetch batch payloads, pending tool calls, concurrent reads | Apply justified host input/admission limits and measure absolute RSS as well as deltas |
| Pool exhaustion | Abandoned generators, slow direct consumers, concurrent history scans | Ensure `aclosing`, bound admission, and inspect checked-out connections before increasing pools |
| Invalid old exchange | Original importer/writer and saved run error | Correct the producing system; preserve evidence and do not skip/delete invalid historical records |
| Cancellation or timeout | Cursor close, durable failed run and remaining database sessions | Confirm cooperative cleanup; reconcile uncertain external work before issuing a new key |

The invocation deadline includes history reading. Cancellation cannot forcibly terminate arbitrary
blocking custom code. Never log transcript bodies, tool arguments or raw database errors merely to
diagnose resource pressure.

## Verification and rollback

Run venv/pytest differential selection and real-database tests in
`tests/unit/test_streaming_context.py`, `tests/integration/test_history_stream.py` and
`tests/integration/test_context_persistence.py`. The independent consumer repeats these against an
installed wheel with native and real framework paths. Verify cursor snapshot consistency, cross-batch
tool relationships, oversized and corrupt rows, cancellation/timeout, competing runs, restart replay,
zero checked-out connections and no surviving tagged sessions.

Use the [installed workload commands](../../validation/history_workload/README.md) for three matched
baseline/candidate repetitions. Include `--stream-history` only for the capable candidate. Compare
short-history latency and every frozen long-history budget; retain all failures and provenance.
CI smoke proves deterministic mechanics, not production capacity or the full resource envelope.

Roll back subsequent runtime instances to `stream_history=False` while retaining the same window.
This restores complete materialization and its measured cost without changing selection semantics,
database schema or persisted data. Successful keys still replay; failed keys remain failed. If
downgrading to v0.6.0, remove the new keyword entirely. Disabling the window is a separate host policy
decision and also restores complete initial agent input.
