# Upgrade to v0.4.0

## Compatibility

Canonical contract `v1` and schema `0002` remain unchanged. No migration is added over v0.2/v0.3.
Existing runtime/provider constructors and manual `shutdown()` retain their documented signatures.
`AgentRuntime.lifespan()` and `RuntimeShutdownTimeoutError` are additive and optional. Provider
completion/decoding hardening from v0.3.1 is retained; older proxies must send valid completed SSE.

For v0.1.1 follow the drained-worker [v0.2.0 procedure](upgrading-to-v0.2.0.md) first. Do not infer
that an unchanged schema makes arbitrary mixed-version deployments or external tools exactly-once.

## Host integration

1. Preserve the application lock/configuration and database backup. Rehearse on an empty disposable
   PostgreSQL database using the actual checksum-pinned source release.
2. Stop/drain ingress, then perform checked shutdown before closing resources or replacing workers.
3. Install the reviewed target wheel/extras in the application's venv and explicitly confirm schema
   revision using the installed migration commands. No constructor runs migrations automatically.
4. Keep manual checked shutdown, or adopt `async with runtime.lifespan(shutdown_timeout_seconds=6)`
   around the serving portion of the application. Construct dependencies outside and close them
   afterwards with `finally`; handle incomplete teardown as an operational failure.
5. Reuse a host-owned bounded engine. Do not create/dispose a pool for every request. Namespace
   filtering remains distinct from authorization and agent-scoped idempotency domains.
6. Verify normal/tools/streaming/history/feedback, failed-key policy and successful restart replay.

The helper cannot reopen a closing runtime or nest concurrent lifetimes. It does not close an
injected engine, model, HTTP client or telemetry handle. `PostgresMemoryStore.close()` still
disposes its engine, including an injected engine; shared-resource owners should dispose once.
See [library integration](library-integration.md) for explicit composition and teardown failure policy.

## Installed upgrade rehearsal

Build the candidate wheel, set `AGENT_RUNTIME_TEST_DATABASE_URL` to a separate empty PostgreSQL
database, and run in the repository venv:

```bash
uv run python scripts/validate_consumer.py \
  --wheel dist/agent_runtime_kit-0.4.0-py3-none-any.whl \
  --expected-version 0.4.0 --upgrade-from-release 0.3.1 --acceptance-only --frameworks \
  --soak-seconds 120 --soak-shared-engine --report-dir build/v040-upgrade
```

The runner checks the published v0.3.1 wheel digest before seeding exact history, canonical replay,
feedback, memory and an active ownership lease. The target must preserve these, continue history,
exercise real framework streaming and pass the optional lifetime/resource checks. The soak requires
that dedicated database to have no unrelated clients. Run per-engine churn separately by omitting
`--soak-shared-engine`; duration is a synthetic test setting, not a service SLA.

After publication repeat with the actual downloaded target wheel and verified asset digest.
Retain inventories, provenance, JUnit and soak reports. Historical v0.1.1/v0.2.0/v0.3.0 upgrades
remain separate CI/release gates, not silently replaced by the newest source version.

## Rollback

Drain/shutdown first and restore the application's previous lock if required. No database downgrade
is needed between these unchanged-schema versions. Remove use of the new lifetime API before
running a v0.3.x wheel. A rollback cannot undo tool effects, recover provisional text as success,
or justify reexecuting failed invocation keys; inspect durable evidence and use explicit recovery.
