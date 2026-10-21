# Independent Framework Applications

These application-owned Runnable and compiled StateGraph implementations use only installed
public library APIs. They do not import `examples`, private kit modules, a persistent checkpointer,
or a provider SDK. The custom `transcript`/`phase` state deliberately exercises explicit mapping.

The runner copies the application and its tests outside the checkout into the existing consumer
project, enables its separately locked `frameworks` dependency group, and installs the supplied
wheel in that project's venv. The baseline run without this flag still installs no frameworks.

```bash
uv build --out-dir build/release-dist
# Set AGENT_RUNTIME_TEST_DATABASE_URL to a disposable PostgreSQL database first.
uv run python scripts/validate_consumer.py \
  --wheel build/release-dist/agent_runtime_kit-0.2.0-py3-none-any.whl \
  --expected-version 0.2.0 --acceptance-only --frameworks \
  --report-dir build/framework-consumer
```

The wheel itself initializes schema `0002`. Each parametrized framework check covers a distinct
boundary: tools/multi-turn/feedback/restart replay, safe framework failure without partial history,
and host shutdown propagating cancellation and settling the durable run. Streaming currently
asserts terminal fallback; incremental framework streaming is a later feature.

Local acceptance: Python 3.12, PostgreSQL 16, LangChain Core 1.6.2, LangGraph 1.2.11,
24 passed, one upgrade-only skip and four historical probes deselected. This includes six
framework cases plus the existing 18 fresh-wheel cases. It is not a broad version certification.
JUnit, package provenance and the installed dependency inventory are saved in the report directory.
