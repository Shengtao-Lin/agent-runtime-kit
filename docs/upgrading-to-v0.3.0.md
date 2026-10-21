# Upgrading to v0.3.0

Published on 2026-10-21; see [the immutable artifact and canary evidence](validation/v0.3.0-publication.md).

## Supported transition

From v0.2.0, the canonical contract remains `v1` and schema head remains `0002`.
Install the v0.3.0 wheel plus only the required extras, explicitly repeat
`python -m agent_runtime.migrations upgrade` in a migration job, and confirm `current` reports
`0002`. Existing history, feedback, memory, successful replay JSON and fenced owner leases remain
unchanged. Do not reset/stamp the database or infer that package version changes need a new schema.

From v0.1.1, first follow [the v0.2.0 drained-worker upgrade](upgrading-to-v0.2.0.md).
The original legacy-active-run settlement policy still applies to that older schema.

## Framework dependency change

The `adapters` extra now requires LangGraph >=1.2.11,<2. Resolve LangChain Core to a version compatible
with the chosen graph and verify your application mappers with synthetic data. An older graph
0.2.28 streaming cancellation probe left an internal queue task pending and is not certified here.
Pin your application's tested dependency set; the library's allowed ranges are not a promise
that every core/graph combination is installable.

## Streaming adoption

Default adapters retain terminal fallback. Enable `streaming=True` deliberately, after checking
the [framework runbook](runbooks/framework-adapters.md#incremental-mode-v030-and-later). Review
graph node filtering and your state output mapper together: filtering deltas does not redact
durable history or the final response. Custom event shapes need an explicit stream mapper.

Close abandoned iterators. During shutdown, drain ingress and call `runtime.shutdown()` before
disposing model clients, database engines and telemetry. Partial deltas remain provisional;
use the same semantic request/key to reconcile uncertain completion, never infer tool rollback.

## Reproducible upgrade acceptance

Set `AGENT_RUNTIME_TEST_DATABASE_URL` to a dedicated disposable PostgreSQL database, then:

```bash
uv build --out-dir build/release-dist
uv run python scripts/validate_consumer.py \
  --wheel build/release-dist/agent_runtime_kit-0.3.0-py3-none-any.whl \
  --expected-version 0.3.0 --upgrade-from-release 0.2.0 --acceptance-only --frameworks \
  --report-dir build/v020-v030-upgrade
```

The runner downloads the published v0.2.0 wheel, verifies its pinned SHA-256, initializes through
its packaged migrations and seeds synthetic evidence in an external venv. It then installs the
target wheel and verifies exact message/replay preservation, feedback, namespaced memory,
unchanged ownership/lease and successful continuation, plus framework streaming. The local result
is 34 passed, four historical probes deselected. CI and Release repeat this path before publishing.

The synthetic owned run is inspected and explicitly settled at the end of the test. No production
database, shared volume or live provider is used. Roll back application artifacts only with an
application-tested deployment plan; there is no new schema downgrade for v0.3.0.
