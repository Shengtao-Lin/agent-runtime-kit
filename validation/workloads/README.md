# Installed-Wheel Host Workload

This validation-only project imports the installed runtime wheel and copies Release Desk's business
modules into a temporary external consumer. It does not import the runtime checkout. Its independent
lock freezes actual published v0.5.0 and adds pytest and psutil solely for acceptance/measurement.
The [workload specification](../../docs/validation/stage-d-workload.md) defines metrics and limits.

Set `WORKLOAD_DATABASE_URL` to a dedicated disposable PostgreSQL database. The runner migrates it;
it does not create/drop the database or delete retained synthetic history. From the root venv:

```bash
python scripts/validate_workload.py --smoke --repetitions 1 --report-dir build/workload-smoke
python scripts/validate_workload.py --repetitions 3 --report-dir build/workload-v050-baseline
python scripts/validate_workload.py --wheel dist/agent_runtime_kit-VERSION-py3-none-any.whl \
  --expected-version VERSION --repetitions 3 --report-dir build/workload-candidate
python scripts/summarize_workload.py build/workload-v050-baseline \
  --output build/workload-v050-baseline/summary.json
```

Use a new report directory for every run. Full mode has fifteen profiles: seven single-axis
history/concurrency/payload combinations in invoke and stream modes, plus a small slow-consumer case.
Every profile/repetition starts a new Python process; warmup/seeding and the measurement window are
separate. The runner bounds each subprocess to 210 seconds, while the experiment uses a 180-second
cooperative deadline and sampled RSS abort guard. It records failures without overwriting evidence.

`manifest.json` records profiles, source hashes and the exact wheel digest; `package-audit.json`
records installed provenance and resolved dependencies; `tests.xml` records fixture/measurement
pytest checks. Each profile JSON retains raw timing/size and resource observations, exact replay
assertions and teardown results. Inputs are synthetic; reports do not include transcripts or secrets.
Compare manifests before comparing timings. RSS and canonical byte measurements are not provider
tokens, wire traffic, storage allocation or production throughput certification.

The initial-context allowance in the specification is a diagnostic host requirement. Baseline runs
record the actual input size even when it exceeds that allowance; they must not pretend to enforce
a policy that the installed version does not implement. Subsequent ADR/contract work owns the fix.

The summary accepts only complete passing profiles with their expected sample counts and cleanup
assertions. Percentiles pool samples across repetitions and use nearest-rank p95; they are descriptive,
not confidence intervals. Raw JSON remains authoritative for repetition-level comparisons.
