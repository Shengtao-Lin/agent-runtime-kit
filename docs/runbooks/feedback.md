# Feedback Runbook

## Responsibility

Feedback is immutable evidence linked to a successful run, assistant message, or tool result. It
does not change prompts, models, routing, tools, or memory.

## Verify

```bash
uv run pytest tests/integration/test_postgres_memory.py -q
```

Every HTTP submission requires the same `Idempotency-Key` in the header and body. Equivalent retries
return the stored record; a different payload with the same key returns `idempotency_conflict`.

## Operational checks

- Confirm the referenced run is `succeeded` and still contains the canonical response.
- Confirm `target_id` belongs to that response.
- Use `supersedes_feedback_id` for corrections; never update the original row.
- Export through paginated repository reads filtered by run, target, source, type, or time.

## Common failures

- `invalid_feedback_target`: refresh the run/output identifiers presented to the reviewer.
- Conflict during retry: preserve both client payloads outside the runtime and investigate the
  caller's idempotency-key generation.
- Missing correction parent: retry only after the original record is durably visible.

Feedback comments may contain sensitive content. Default telemetry records identifiers and
categories only.
