# Release Runbook

## Purpose and Ownership

Use this runbook to prepare and verify an importable library release, publish existing approved
artifacts through GitHub Actions, and handle failures without silently changing published history.
The latest published version is v0.4.1. Version metadata is not publication; never move an existing tag.

## Preconditions

- Main and the exact candidate PR commit have green CI; no unrelated local changes are included.
- Package metadata and `uv.lock` agree. Changelog and versioned release notes describe breaking
  changes, schema revision, rollback limits, and distribution channels.
- [The v0.2.0 upgrade guide](../upgrading-to-v0.2.0.md) has been rehearsed with preserved evidence.
- [The v0.3.0 upgrade guide](../upgrading-to-v0.3.0.md) covers the checksum-pinned current-release
  path; preserved owner leases and post-upgrade framework streaming must pass.
- Repository permissions permit GitHub Releases and GHCR. PyPI publishing is intentionally absent.
- Publication and its target version/commit are authorized by the repository owner.

## Verification Gates

Normal CI uses locked venv environments and pytest for Linux/Windows unit tests, PostgreSQL
integration, wheel imports, independent baseline/fresh/upgrade consumers, and container smoke.
The Release workflow repeats quality/full pytest, builds distributions once, and passes that exact
wheel artifact to a separate independent-consumer job. Fresh installation and upgrades from
v0.1.1/v0.2.0/v0.3.0/v0.3.1/v0.4.0/v0.4.1
must pass, alongside the release container smoke job, before either publishing job can run.
Provider acceptance installs that wheel with only provider extras into another external venv.
The v0.4+ fresh consumer also runs sustained-operation task/pool/database teardown checks. The
lower/latest compatibility workflow exercises the shared-engine mode and retains memory reports
without treating synthetic allocation samples as a production SLA.
Container verification includes real in-flight SIGTERM drain/cancellation and post-restart replay.
It builds matching app/migration images and retains evidence until job cleanup.

Changes to release metadata/workflow/notes also run this verification path on pull requests.
Manual `workflow_dispatch` runs verify without publishing, including when dispatched at a tag.
Publishing requires the event to be a tag **push**, not merely a tag-shaped ref. No manual job
starts a release or pushes an image. Preserve the candidate's exact SHA and result links.

Use absolute tag-pinned links in versioned notes, because GitHub renders relative release-body
paths differently from repository Markdown. Owner-authorized synchronization runs after all three
verification jobs on main pushes affecting versioned notes or the release workflow. It updates only
the existing release body's notes for the current package version; it does not create a release,
move tags, replace assets or publish images. Missing notes or an unreadable/unpublished release
produce a documented no-op. Pull requests and manual runs cannot synchronize metadata. After a
correction, verify the actual GitHub release body and rendered links rather than inferring success
from the repository file or a skipped/no-op job.

Local rehearsal in the project venv:

```bash
uv sync --locked --all-extras --dev
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
uv build --out-dir build/release-dist
```

Use migrated disposable PostgreSQL for root tests. Run `scripts/validate_consumer.py` with the
candidate wheel, explicit expected version, and `--acceptance-only` on a fresh disposable database.
Repeat with `--upgrade-from-baseline` on a separate empty database. The frozen baseline is still
v0.1.1 and intentionally records four historical expected failures; those are not candidate passes.

## Publication

1. Verify the final main SHA and non-publishing Release run are green. Stop if checks fail or the
   target version is already published; do not overwrite/move an existing tag.
2. Create/push the authorized version tag at that exact verified commit. It must match installed
   package metadata and must not already exist; the workflow verifies that match.
3. Monitor `Verify release`, `Verify release container`, and `Verify release wheel and published
   upgrades`. Both publishing jobs depend on all three.
4. Confirm the GitHub Release has the wheel and source archive, and GHCR has the versioned image.
   Versioned notes under `docs/releases/<tag>.md` override generated notes when present.
5. Download/install the actual published wheel into a new consumer venv and repeat a synthetic
   migration/invoke/replay canary. Report artifact/workflow links, not just a successful tag push.
6. Update the README's latest-published status and move the release-candidate changelog heading
   to the verified release date. Never claim publication from version metadata alone.

## Failures and Recovery

If verification fails, neither publication job should run. Fix through a PR and choose a new
approved immutable release commit/tag as appropriate; do not force-update published tags.
If only one publishing job succeeds, inspect the exact run before retrying that job. A GitHub
Release may already exist; do not blindly rerun `gh release create` or republish different bytes
under an existing version. Surface partial publication explicitly.

Application/database rollback is separate from artifact publication. Follow the upgrade guide,
drain workers, and use a verified restore plan. Never automatically downgrade production or delete
volumes. A schema downgrade cannot undo tool effects or resurrect settled runs.

## Evidence and Escalation

Collect commit/tag, workflow/job URLs, package version, migration revision, JUnit results, wheel
provenance and dependency inventory. Do not collect tokens, provider credentials, customer content,
or database URLs in release notes. Account-level package permissions and PyPI trusted-publishing
configuration require repository-owner intervention; tests use no live provider credentials.
