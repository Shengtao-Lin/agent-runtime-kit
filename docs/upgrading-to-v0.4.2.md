# Upgrade to v0.4.2

## Compatibility and intentional behavior changes

Public signatures, dependency ranges, canonical contract `v1` and schema `0002` are unchanged.
No database migration is added. From v0.1.1, follow the drained-worker
[v0.2.0 procedure](upgrading-to-v0.2.0.md) first. Check the README for publication status; a version
in source metadata is not evidence that a release has been published.

Native invocation usage now sums every model call, including tool selection. Unknown usage in any
call makes the whole invocation total `None`; explicit zero is retained. Historical stored/replayed
responses keep their original values and are not recalculated. Framework-adapter envelopes are
unchanged. Adjust dashboards that previously treated native final-answer usage as whole-run usage;
neither behavior is a monetary billing estimate.

Provider adapters reject malformed supported tool fields and duplicate IDs within one completion
with safe non-retryable `ProviderError`. Inspect proxy fixtures for previously coerced IDs/names,
non-string OpenAI JSON arguments, non-object Anthropic/Gemini input, and invalid streaming indices.
OpenAI nullable stream placeholders and fragmented JSON arguments remain supported. Valid calls
with identical names/arguments but different IDs are not deduplicated.

Malformed output prevents local execution of every tool in that model result. Already executed
tools from an earlier valid loop iteration are not rolled back. A failed run stores no partial
history or success response; its idempotency key does not silently retry. Stream deltas are
provisional and full exception causes remain internal diagnostics.

## Rehearsal and rollout

1. Save the host lock/configuration and backup. Drain ingress and complete runtime shutdown before
   replacing workers; close injected resources only after cleanup.
2. Verify the target wheel checksum, installed version and non-editable path in the host venv.
   Install only needed extras and confirm schema `0002` using the installed migration command.
3. Rehearse direct generate/stream and multi-round tool loops with approved synthetic fixtures.
   Check unknown totals, malformed tool failures, successful storage and exact replay.
4. On a disposable PostgreSQL database, preserve actual published v0.4.1 state before upgrading:

```bash
uv run python scripts/validate_consumer.py \
  --wheel dist/agent_runtime_kit-0.4.2-py3-none-any.whl \
  --expected-version 0.4.2 --upgrade-from-release 0.4.1 --acceptance-only --frameworks \
  --soak-seconds 30 --soak-shared-engine --report-dir build/v042-upgrade
uv run python scripts/validate_providers.py \
  --wheel dist/agent_runtime_kit-0.4.2-py3-none-any.whl \
  --expected-version 0.4.2 --report-dir build/v042-providers
```

The source v0.4.1 wheel is SHA-256 pinned before installation. Retain inventories, preserved-state
evidence, JUnit and soak reports. Repeat with the actual downloaded target asset after publication,
not a local rebuild. Historical upgrade gates remain mandatory. These offline checks do not replace
an account/model-specific, privacy-reviewed canary with an approved spend cap.

## Rollback

Drain workers and restore the previous host lock if needed; no schema downgrade is required.
Rollback restores final-call-only native usage and weaker provider tool validation. It does not
rewrite historical responses, undo tool effects or authorize blind reexecution of failed keys.
