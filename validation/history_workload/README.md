# Installed-Wheel Long-History Workload

Stage E uses a separate lock pinned to actual v0.6.0. The runner copies this validation project,
Release Desk business modules and the original Stage D seed helpers outside the checkout. Only
the installed wheel supplies library behavior. The [frozen specification](../../docs/validation/stage-e-workload.md)
defines budgets, profiles, measurement boundaries and completion criteria.

Set `WORKLOAD_DATABASE_URL` to an isolated disposable PostgreSQL database. Use the root venv:

```bash
python scripts/validate_history_workload.py --smoke --repetitions 1 --report-dir build/history-smoke
python scripts/validate_history_workload.py --repetitions 3 --report-dir build/history-v060-baseline
python scripts/validate_history_workload.py --wheel dist/agent_runtime_kit-VERSION-py3-none-any.whl \
  --expected-version VERSION --repetitions 3 --report-dir build/history-candidate
```

Every profile/lane/repetition starts a fresh process. Timing runs omit tracemalloc, full-history
diagnostic serialization and per-row/SQL timers. Diagnostic runs use
[SQLAlchemy cursor events](https://docs.sqlalchemy.org/en/20/faq/performance.html#query-profiling)
and [Psycopg JSON loaders](https://www.psycopg.org/psycopg3/docs/api/types.html#psycopg.types.json.set_json_loads)
around the installed implementation; they do not replace its query or decoder behavior. Server
EXPLAIN runs separately. Nested timings and logical JSON bytes are not independent network timings
or wire-byte counts. Tracemalloc and sampled RSS have different coverage and overhead.

A `passed` raw report means the experiment and correctness assertions completed. Its `host_budgets`
may still contain failures: that is precisely the baseline evidence used to justify optimization.
Do not discard these reports, reinterpret diagnostic timing as low-interference latency, or relax
the frozen budgets. Failed/aborted profiles remain failures. Never overwrite a manifest directory.

The runner retains raw samples, manifest/source hashes, independent package audit and pytest JUnit.
Reports contain no transcript bodies or SQL predicates. Original Stage D archives and lock remain
unchanged. Large experiments are local evidence; deterministic smoke is suitable for CI.
