# Model Input Boundary Validation

This external consumer observes actual published v0.7.0 and the independent Release Desk host.
The [frozen Stage F specification](../../docs/validation/stage-f-workload.md) owns acceptance and
safety limits. The first normal-host baseline does not implement a guard or establish a library gap.

Set `MODEL_INPUT_DATABASE_URL` to a migrated-or-empty, owned disposable PostgreSQL database. From
the repository root, with uv available and the root venv installed:

```powershell
.venv/Scripts/python scripts/validate_model_inputs.py --report-dir build/stage-f-baseline-new
```

The runner creates a fresh external venv, verifies locked/downloaded/installed wheel identity,
migrates explicitly, and uses pytest to observe sixty reviews per invoke/stream mode. It preserves
per-call message/tool sizes, history/replay/resource assertions, RSS safety observations, dependency
inventory and exact measured sources. A report directory containing a manifest cannot be reused.

This fixture uses deterministic offline release metadata; it never calls a provider or GitHub during
agent execution. Package installation/download is a separate setup step. Current results and their
limits are recorded in the [normal-host baseline report](../../docs/validation/stage-f-release-desk-baseline.md).
