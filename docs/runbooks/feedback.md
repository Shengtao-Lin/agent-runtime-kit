# Feedback Runbook

## Purpose and Ownership

Feedback is append-only evidence linked to a successful run, assistant message, or tool result. It
supports user reactions, reviewer judgments, corrections, comments, and labels. Recording feedback
does not change prompts, models, routing, tools, memory, or a completed runtime response.

## Prerequisites

- Install and configure the PostgreSQL-backed feedback store.
- Apply the current Alembic migration.
- Retain the `run_id` plus the message or tool-result identifiers returned by a successful response.
- Define application authorization for who may submit and search feedback.

## Feedback Contract

| Field | Allowed values or rule |
| --- | --- |
| `target_type` | `run`, `message`, or `tool_result` |
| `source` | `user`, `reviewer`, or `system` |
| `feedback_type` | `thumb`, `rating`, `correction`, `comment`, or `label` |
| `target_id` | Must belong to the referenced successful run |
| `idempotency_key` | Stable for equivalent retries and unique for different submissions |
| `supersedes_feedback_id` | Set on a correction or replacement record; the original remains immutable |

The `value` must match the selected feedback type's intended application convention. Document
rating scales and label vocabularies in the consuming application so downstream analysis remains
comparable.

## Python Workflow

```python
from uuid import uuid4
from agent_runtime.feedback import FeedbackSubmission, PostgresFeedbackStore

feedback = PostgresFeedbackStore(engine, telemetry)
submission = FeedbackSubmission(
    feedback_id=uuid4(),
    idempotency_key="feedback-request-123",
    run_id=response.run_id,
    target_type="message",
    target_id=str(response.message.id),
    source="user",
    feedback_type="thumb",
    value=True,
)
record = await feedback.record(submission)
```

Use `search()` for paginated repository reads filtered by run, target, source, feedback type, or
time window. Treat cursors as opaque values and continue until the returned page has no next cursor.

## HTTP Workflow

The header and body idempotency keys must match:

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/feedback \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: feedback-request-123' \
  -d '{"feedback_id":"00000000-0000-4000-8000-000000000001","idempotency_key":"feedback-request-123","run_id":"00000000-0000-4000-8000-000000000002","target_type":"message","target_id":"00000000-0000-4000-8000-000000000003","source":"user","feedback_type":"thumb","value":true}'
```

Replace the example identifiers with values returned by an actual successful invocation. Equivalent
retries return the stored record. A different payload with the same key returns
`idempotency_conflict`.

## Correction Workflow

Never update or delete an incorrect feedback row to make a correction. Submit a new record with a
new feedback ID and idempotency key, and set `supersedes_feedback_id` to the original record. This
preserves the audit trail and makes downstream reconstruction deterministic.

## Verification and Operational Checks

```bash
uv run pytest tests/integration/test_postgres_memory.py -q
```

- Confirm the referenced run is `succeeded` and retains the canonical response.
- Confirm `target_id` belongs to that response and matches `target_type`.
- Replay an equivalent submission and confirm it returns the original record.
- Attempt a changed payload with the same key and confirm it is rejected.
- Verify a correction points to a durably visible parent.
- Monitor submission counts, categorical failures, and query latency without recording comment text.

## Troubleshooting

### `invalid_feedback_target`

Refresh the run and output identifiers presented to the reviewer. Feedback cannot target a failed
or active run, a message from another response, or an unrelated tool result.

### Header/body key conflict

Use the same `Idempotency-Key` value in both locations. Generate it once at the client boundary and
pass it through unchanged.

### Conflict during retry

The caller reused a key for a different payload. Preserve both sanitized payloads or hashes outside
the runtime for diagnosis, fix key generation, and issue a new key for the new submission.

### Correction parent is missing

Retry only after the original record is durably visible. Confirm the parent ID and database target;
do not silently record an unlinked replacement.

### Feedback is absent from analysis exports

Verify time-zone boundaries, filters, cursor continuation, and whether the export intentionally
includes superseded records. The store is append-only; consumers decide how corrections are folded.

## Data and Security Notes

Comments and corrections may contain sensitive or adversarial text. Default telemetry records
identifiers and categories only. Apply authorization to both submission and search, define
retention explicitly, sanitize data before exports, and never treat feedback text as trusted model
instructions.

## Related Documentation

- [User Runbook](../user-runbook.md)
- [HTTP Service](http-service.md)
- [PostgreSQL](postgres.md)
- [Architecture Decision 0004](../adr/0004-feedback-capture-boundary.md)
