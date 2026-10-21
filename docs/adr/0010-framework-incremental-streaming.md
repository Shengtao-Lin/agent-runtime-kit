# ADR 0010: Opt-In Framework Incremental Streaming

Status: Accepted

## Context

The canonical runtime already streams text/tool-result events and atomically persists one final
response. Framework wrappers historically called only `ainvoke()`. Applications need incremental
framework output without changing default mappings or introducing framework objects into the core.

## Decision

Add opt-in `streaming=True` and an optional explicit `stream_mapper` on both wrappers. Keep
`invoke()` and default terminal fallback compatible. No new canonical event/schema version is
needed. Framework imports in the default translators remain lazy.

For Runnable streams, combine public `AIMessageChunk` objects and convert the aggregate with the
public `message_chunk_to_message` helper before normal output mapping. Support one message stream
or one terminal state value. Ambiguous multi-message/multi-state streams require an explicit mapper.

For graphs, use the documented v1 tuple API with `messages` and `values` modes, supported across
the tested older/current combinations. Forward text chunks, retain the latest state, and map it
once the source ends. Provide an explicit metadata predicate to restrict deltas to public nodes.
This predicate is not a history/final-response privacy filter. Persist no framework checkpointer.

Default tool results come from the final canonical output; custom stream mappers can emit earlier
results while retaining a complete final output. Every mapped stream must have exactly one
completion and no later events. Runtime completion is emitted only after normal hooks and atomic
commit. Partials remain provisional. Replays do not re-stream model work.

Close underlying iterators deterministically on exceptions, cancellation and abandoned consumers,
including cancellation while the runtime queue is full. No extra unbounded queue is introduced.
Framework buffering, blocking extensions and tool side effects remain outside hard cancellation
or exactly-once guarantees.

## Consequences and Evidence

Applications enable streaming deliberately and own custom state/event mapping and node selection.
Unsupported multimodal/reasoning blocks fail rather than leaking or silently discarding content.
Full graph snapshots stay in process and are not forwarded as transport events or telemetry.

Root pytest exercises real Runnable generators, fragmented tool arguments, usage, safe failures,
terminal validation, graph node filtering and iterator close. Independent installed-wheel tests
exercise real Runnable/StateGraph token delivery before completion, atomic persistence/replay,
partial failure and cancellation. Compatibility probes run the same application at lower/latest
allowed dependencies. Default example containers retain their documented terminal fallback.

## Public References

- https://docs.langchain.com/oss/python/langgraph/streaming
- https://reference.langchain.com/python/langchain-core/messages
