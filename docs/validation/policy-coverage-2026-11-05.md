# Policy Composition Acceptance — 2026-11-05

Stage B3 adds executable evidence for existing lifecycle semantics without changing public hook
names or runtime execution. Base source is `bc3f59e` (PR #37); the candidate remains unreleased.

## Coverage

- Native invoke/stream: four phases occur once around a two-call model/tool loop; each block point
  settles as blocked with no new durable history/response. Successful and blocked replay bypass hooks.
- Native tool/output-hook failure and cancellation: failed records, empty history, no reexecution.
- Native output block after streaming: provisional text/tool results are visible before the block;
  no public completed event or successful durable response is produced.
- Real LangChain Runnable and LangGraph StateGraph, invoke/terminal-stream fallback: shared runtime
  and registry hooks, every block point, effect counts, blocked replay and successful replay.
- Framework tools called directly by the host bypass registry hooks while runtime hooks still run.
- Unit checks require both registry hook configuration and context, stop later hooks on a block,
  and distinguish pre-handler blocking from post-effect blocking.

## Local evidence

Docker Engine 29.7.2 ran isolated Compose project `ark-v050-validation` with PostgreSQL 16 and
migrations through `0002`. Tests used venv/pytest and synthetic local tools, with no live provider.

- Eight focused hook unit tests passed; Ruff and strict Pyright passed.
- `scripts/validate_consumer.py` with the candidate wheel, expected version `0.4.2`,
  `--acceptance-only --frameworks --report-dir build/policy-consumer` installed a non-editable
  candidate outside the checkout: 71 passed, 1 skipped, 4 deselected.
- The skip is the historical upgrade-only test on a fresh consumer; four readiness probes are
  intentionally excluded by acceptance-only mode. This is not an upgrade-validation result.
- `package-audit.json`, dependency inventory and JUnit results are retained in the report directory.

CI runs these fixtures through the existing fresh/upgrade consumer and lower/latest dependency
paths. Passing that CI is required before merging. Published v0.4.2 artifacts are unchanged.

## Limits

The streaming framework policy cases use terminal fallback; existing incremental mapper tests are
separate. Hooks are cooperative application code and external side effects are not transactional.
An output block does not retract delivered stream content. These tests do not establish universal
framework interception, production authorization or pre-disclosure moderation.
