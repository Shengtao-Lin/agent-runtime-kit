# Release Desk

A separately packaged, read-only CLI for reviewing public Python release metadata. It consumes
the published Agent Runtime Kit wheel; the application is not part of the runtime distribution.
The application owns its GitHub reader, checklist, routing, configuration and resource lifecycle.
Its own lock pins the actual v0.4.0 wheel and digest, not an editable checkout. It adopts the public
checked runtime lifetime while retaining ownership of its engine and business logic.

This is a deliberately deterministic operational tool, not an LLM-quality demonstration. It needs
no provider account. For each requested stable tag, it checks tag identity, published/non-prerelease
status, a universal Python wheel/source archive and recorded SHA-256 digests. Its verdict is
`metadata ready` or `metadata incomplete`, not a security review, CI attestation or proof that the
artifact bytes were installed. It never publishes, edits GitHub, executes downloaded code or sends
feedback to repository maintainers.

## Install and configure

Requires Python 3.11/3.12, uv and PostgreSQL 16. From this application's directory:

```bash
uv sync --locked --all-groups
```

Set `RELEASE_DESK_DATABASE_URL` to a PostgreSQL Psycopg async URL for your application database.
Example for the repository's local synthetic PostgreSQL service, run from repository root:

```bash
docker compose exec -T postgres createdb -U agent_runtime release_desk
```

PowerShell, then return to this application's directory:

```powershell
$env:RELEASE_DESK_DATABASE_URL="postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/release_desk"
$env:RELEASE_DESK_NAMESPACE="my-release-desk"
uv run release-desk migrate
```

POSIX shells use `export RELEASE_DESK_DATABASE_URL=...` and `export RELEASE_DESK_NAMESPACE=...`.
The explicit migration command initializes the installed wheel's schema `0002`; reviews never
auto-migrate. Secrets are read from environment settings and not printed in CLI error messages.

## Daily workflows

```bash
uv run release-desk review Shengtao-Lin/agent-runtime-kit v0.4.0 --key release-review-v040-001
uv run release-desk review Shengtao-Lin/agent-runtime-kit v0.4.0 --key release-review-v040-002 --stream
```

Normal mode prints the canonical JSON response; stream mode prints canonical JSON events as
lines. Record `run_id`, `thread_id` and `message.id` from the response/completed event. The default
distribution is `agent-runtime-kit`; specify `--distribution` for another package name. Only stable
`vMAJOR.MINOR.PATCH` tags and universal `py3-none-any` wheels are in this checklist's scope.

Use `--thread THREAD_UUID` with a new key to continue the same history. Retry exactly the same
semantic command/key to retrieve the original durable response after restart, without fetching
GitHub again. A changed tag/thread/distribution with the same key is a conflict. To refresh a
release's metadata after assets change, deliberately use a new key. Failed keys are not silently
rerun. First-pass text deltas are provisional until the completed event arrives.

```bash
uv run release-desk history THREAD_UUID
uv run release-desk feedback RUN_UUID MESSAGE_UUID --key feedback-001 --value helpful
```

Feedback is local append-only evidence. Equivalent CLI retries derive the same feedback UUID and
scoped key, preventing duplicate records across processes. Changing its value/target with the same
key conflicts. It does not change the router or checklist and is not sent to GitHub.

Exit status: 0 for metadata ready/successful maintenance, 1 for an incomplete checklist, 2 for
configuration/dependency/contract failure, 130 for interruption. Error messages omit raw database
and network details. Inspect safe durable run categories through the library for deeper diagnosis.

## Boundaries and operations

This is a trusted local operator CLI, not a multi-user service. Namespace filtering is not identity
or tenant authorization; database access and user permissions belong to the host. Agent IDs and
feedback key domains are derived from the application namespace to avoid accidental cross-scope
idempotency collisions. Use separate databases where trust boundaries require them.

The GitHub reader uses the public API with no token, a 10-second network timeout and a 1 MB response
limit. Rate limits, private repositories and missing releases produce an operational failure, not
an invented readiness verdict. Review bodies, credentials and arbitrary GitHub metadata are not
copied into the result. A requested namespace is stable across application restarts.

Database pools are bounded to two connections without overflow. Runtime/tool budgets are 30/12
seconds. The checked `runtime.lifespan(shutdown_timeout_seconds=6)` exits before engine disposal
and raises on incomplete shutdown. Body failures still close the runtime and application-owned
engine; this is not a hard kill or rollback. Cancellation is cooperative;
the bounded blocking HTTP reader may finish after its async caller has been cancelled. It performs
no external writes. No background scheduler, model credentials or telemetry exporter is configured.

## Verification

The isolated runner copies this application outside the checkout and uses its own locked venv.
Tests reject a source-checkout or editable runtime library. Synthetic release metadata makes CI
independent of GitHub availability and credentials. Real PostgreSQL verifies tools, history,
streaming, restart replay, feedback and resource teardown.

From repository root, point `RELEASE_DESK_DATABASE_URL` at a disposable database, then:

```bash
uv run python scripts/validate_release_desk.py
```

Add `--live` for one public metadata review of this repository's v0.4.0, followed by cross-process
replay, history and idempotent feedback checks. It makes no provider calls and no GitHub writes.
The runner saves JUnit, package inventory and optional canary evidence under
`build/release-desk-validation/`. `--wheel PATH` is an explicitly non-locked candidate override
for pre-publication development; published acceptance uses the lock and actual release wheel.
