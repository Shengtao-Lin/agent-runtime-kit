# Runtime Runbook

## Responsibility

`AgentRuntime` resolves an invoker, claims a durable run, loads history, applies hooks, enforces the
total deadline, persists canonical messages, and commits the replayable response. It supports both
`invoke()` and `stream()` without exposing framework or provider objects.

## Install and verify

```bash
uv sync --all-extras --dev
uv run pytest tests/integration/test_runtime.py -q
```

For `stream()`, consume events until `completed`. The supported event order is `started`, zero or
more `text_delta` or `tool_result` events, then `completed`. A caller that stops consuming must
close the async iterator so cancellation can mark the run failed.

## Operational checks

- Confirm the requested agent appears in `AgentRegistry.list_descriptors()`.
- Look up the run by its idempotency key and inspect its categorical status and safe error code.
- Confirm the thread exists in the same configured namespace.
- Compare `INVOCATION_TIMEOUT_SECONDS` with downstream provider and tool deadlines.

## Common failures

- `unknown_agent`: register the agent before accepting traffic.
- `thread_not_found`: reject stale caller state; never silently create a replacement thread.
- `run_in_progress`: retry the same idempotent request with backoff.
- `idempotency_conflict`: generate a new key for a semantically different request.
- `invocation_timeout`: inspect provider latency and tool spans before increasing the bound.

Do not edit run rows manually. Fix the owning dependency and retry with a new idempotency key when
the prior run is terminal and unavailable for replay.
