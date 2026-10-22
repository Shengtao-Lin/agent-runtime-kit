# Upgrade to v0.4.1

## Compatibility and intentional boundary changes

Public signatures, dependency ranges, canonical contract `v1` and database schema `0002` are
unchanged from v0.4.0. No migration is added. Managed lifetime remains optional and host resources
remain application-owned. For v0.1.1 use the drained-worker [v0.2.0 procedure](upgrading-to-v0.2.0.md).

Direct provider adapters now translate canonical text/message limits to safe `ProviderError`
with `retryable=false`, including streaming final assembly. Deltas are provisional, never proof of
success. The 32,000-character text/64-part message limits are unchanged, not increased to transport
limits. Full exception chains are internal diagnostics, not public-safe serialization.

Unknown usage now stays `None` when either count is absent/null. Explicit integer zero is retained;
negative, boolean, floating-point and numeric-string counts or non-object usage fail safely. Hosts
must not assume that missing usage means free/zero tokens. Anthropic start/delta counts are merged
and cumulative output counts replace, not add to, earlier values.

OpenAI/Gemini accept one choice/candidate; alternatives and supplied indices other than integer zero
fail rather than merging/dropping content. Omitted indices and OpenAI empty-choice usage chunks
remain supported. Review proxies against this supported subset before rollout. No automatic
provider retries or continuation are introduced; a failed runtime key is still not reexecuted.

## Rehearsal and rollout

1. Save the host lock/configuration and database backup; stop/drain ingress and perform checked
   shutdown before replacing workers. Do not close shared resources before runtime cleanup.
2. Install the reviewed target wheel and only needed extras in the host venv. Verify its checksum,
   installed version/module path and schema revision using installed migration commands.
3. Exercise direct generate/stream calls with representative approved proxy fixtures. Validate
   `usage=None` handling, complete versus provisional output and safe error serialization.
4. Verify history, replay, feedback and owned leases on a dedicated disposable PostgreSQL database:

```bash
uv run python scripts/validate_consumer.py \
  --wheel dist/agent_runtime_kit-0.4.1-py3-none-any.whl \
  --expected-version 0.4.1 --upgrade-from-release 0.4.0 --acceptance-only --frameworks \
  --soak-seconds 30 --soak-shared-engine --report-dir build/v041-upgrade
uv run python scripts/validate_providers.py \
  --wheel dist/agent_runtime_kit-0.4.1-py3-none-any.whl \
  --expected-version 0.4.1 --report-dir build/v041-providers
```

The source v0.4.0 wheel is SHA-256 pinned before seeding; preserve its upgrade evidence and target
inventories/JUnit/soak reports. Repeat after publication with the actual downloaded target wheel,
not a rebuild. Historical upgrades remain CI/Release gates. No credentialed model canary is implied.

## Rollback

Drain/check shutdown and restore the previous host lock if required; no schema downgrade is needed.
Rollback reintroduces the older adapter's weak validation/usage behavior. It does not undo external
tool effects, turn partial output into success or permit blind reexecution of failed keys.
