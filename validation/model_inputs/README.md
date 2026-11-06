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
`release_desk_source` freezes the host from commit `4b896d7708431e90013f482ec2a82777c671f730`;
`host-source.json` checks each source file before execution. Later changes to the real application's
configuration cannot silently change this baseline. This fixture is validation-only and is never
included in the library wheel.

This fixture uses deterministic offline release metadata; it never calls a provider or GitHub during
agent execution. Package installation/download is a separate setup step. Current results and their
limits are recorded in the [normal-host baseline report](../../docs/validation/stage-f-release-desk-baseline.md).

The same external environment now tests the application-owned `model_inputs.py` module, copied
from Release Desk source and hashed in the manifest. It exercises protected native generate,
stream and generate-only fallback, boundary values, durable errors, effect preservation, deadlines,
cancellation and iterator closure. The frozen host is also composed with this wrapper in a separate
test variant; its original source remains unchanged. Baseline tests intentionally observe overflow,
while protected tests require rejection before delegate dispatch. They are never conflated.

Add `--benchmark` for the frozen local performance acceptance. After correctness finishes, the
runner serially launches three independent baseline/candidate timing pairs and one separate
allocation pair. Each process tests 1 KiB, 16 KiB and 64 KiB messages in generate/stream modes.
Every timing profile records 500 calls after 50 warmups; JSON contains all raw observations.
RSS safety reports identify each test and measurement process. Avoid concurrent local workloads.
CI runs correctness only; local overhead thresholds are not asserted on shared hosted runners.
