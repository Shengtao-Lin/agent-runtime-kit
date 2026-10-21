# Upgrade to v0.3.1

## Scope

This patch hardens provider protocol handling without changing canonical contract `v1` or schema
`0002`. No migration is added over v0.2.0/v0.3.0; install the target wheel in a new application venv
and run the packaged migration command explicitly to confirm the schema is current.

For v0.1.1 first follow the [v0.2.0 upgrade guide](upgrading-to-v0.2.0.md), including draining old
workers. The release gate preserves records from v0.1.1, v0.2.0 and the SHA-256-pinned v0.3.0 wheel.

## Before switching

1. Preserve the application lock/configuration and database backup; choose a disposable database
   for rehearsal. Do not downgrade or delete production data as part of testing.
2. Verify that approved provider proxies send protocol completion and valid blank-line-delimited
   SSE. Review the [provider runbook](runbooks/model-providers.md) for finish reasons and limits.
3. Review application retry logic. `retryable` is a hint, not permission to repeat tools or reuse
   a failed invocation key. Authentication/request and malformed-response errors now return false.
4. Stop accepting new work and drain the application using `runtime.shutdown()` before closing
   model clients and the database engine. Replace the locked runtime dependency and restart.

## Rehearsal and acceptance

From the repository venv, build the candidate wheel and point `AGENT_RUNTIME_TEST_DATABASE_URL`
at an empty disposable PostgreSQL database, then run:

```bash
uv run python scripts/validate_consumer.py \
  --wheel dist/agent_runtime_kit-0.3.1-py3-none-any.whl \
  --expected-version 0.3.1 --upgrade-from-release 0.3.0 --acceptance-only --frameworks \
  --report-dir build/v031-upgrade
```

This downloads and digest-checks the real v0.3.0 wheel before seeding history, canonical replay,
feedback, memory and an existing lease. The target must preserve evidence and continue correctly.
Also verify provisional text followed by EOF creates a failed run with no partial history or
successful response; equivalent failed-key retries must not execute the provider again.

After publication repeat using the actual downloaded v0.3.1 wheel, not just a candidate build.
Compare the asset digest and installed provenance. Existing successful replay responses remain
replayable without executing a model. Rollback cannot undo external tool effects; the older provider
parser may accept incomplete output, so restoring it also restores that known limitation.
