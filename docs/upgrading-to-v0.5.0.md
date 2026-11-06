# Upgrade to v0.5.0

## Compatibility and scope

v0.5.0 is published; see [actual-asset evidence](validation/v0.5.0-publication.md). Public import
paths, documented call signatures, canonical contract `v1` and PostgreSQL schema `0002` are retained.
There is no new database revision or dependency-range change. Stricter validation can reject custom
extensions that appeared to work under v0.4.2; this is an intentional behavioral change.

The host still owns prompts, model/tool configuration, authentication and resource lifetime.
Framework wrappers retain framework-owned execution. No distributed worker, resumable workflow,
automatic retry, summarization or context-selection policy is added.

## Changes custom extensions must satisfy

### Streams must finish after exactly one completion

Custom model and agent streams must emit one canonical completion, emit nothing afterward, then
reach normal EOF and finish iterator cleanup. The runtime/native boundary withholds completion
until that happens. Yielding a completion and then waiting forever now times out instead of being
accepted as successful; a trailing event or cleanup error fails the invocation. Close underlying
iterators/resources in `finally`, and propagate cancellation rather than swallowing it.

Valid deltas remain provisional and may reach a caller before a later failure. The runtime emits
its public completed event only after durable success. Invalid model completion cannot dispatch
tools; invalid invoker completion cannot commit successful history. These checks apply when the
extension is called through kit boundaries, not to arbitrary direct host calls.

### Model results must describe one consistent assistant response

Return an assistant `ModelResult.message`. Its ordered `ToolCallContent` values must match
`ModelResult.tool_calls` in ID, name and arguments, with unique IDs within the result. Populate both
representations for a tool-selection response; do not put executable calls only in one list.
Empty arguments remain valid. Distinct IDs with identical names/arguments remain distinct calls.

The native invoker revalidates and snapshots the complete result before dispatch/history/usage in
invoke, streaming and generate-only fallback. Mutating provider-owned objects afterward does not
change that accepted snapshot. Invalid custom results become non-retryable provider failures.

### Tool payloads must already be finite JSON values

Use string-keyed dictionaries, lists, strings, booleans, null and finite numbers. Convert datetime,
UUID, Decimal or other Python objects explicitly before returning or submitting them. Tuples and
non-string dictionary keys are not silently coerced, and NaN/infinities are rejected. Do not rely on
Python equality to treat boolean and numeric tool arguments as interchangeable representations.

Registry argument validation precedes the handler; result validation follows it. A failed result
cannot undo effects the handler already performed. Previous successful loop iterations are also
not rolled back. Failed/blocked runs have no successful replay and their keys do not silently retry.

### Policy composition and imports remain explicit

`before_model`/`after_model` still wrap the whole invoker once, not each model call. Tool phases
require a configured registry and runtime context; framework-native tools can bypass that registry.
Output policy may block after provisional stream output or tool effects. Pre-disclosure moderation
requires host buffering/filtering. Native whole-invocation usage and framework usage envelopes are
unchanged from v0.4.2, and stored historical responses are never recalculated.

Concrete adapter exports now load on explicit request. Existing `from agent_runtime.adapters import
NativeAgentInvoker` and direct module imports retain class identity. Hosts should not rely on
concrete implementations appearing in `sys.modules` merely from importing the core package.

## Rehearsal and rollout

1. Save the host lock/configuration and database backup. Drain ingress, finish runtime shutdown,
   and then close host-owned resources before replacing workers.
2. Install a checksum-verified target wheel into a venv with only needed extras. Verify installed
   version, non-editable provenance and migration revision using the packaged migration command.
   From v0.1.1, also follow the drained-worker [v0.2.0 schema procedure](upgrading-to-v0.2.0.md).
3. Test custom invoker/model streams through EOF and cleanup, including cancellation; test tool
   results with real host serialization shapes. Review [public contracts](public-api.md) and run
   the host's own pytest suite.
4. Use separate disposable databases for fresh installation and preserved-state upgrade. Set
   `AGENT_RUNTIME_TEST_DATABASE_URL` before running:

```bash
uv run python scripts/validate_consumer.py \
  --wheel dist/agent_runtime_kit-0.5.0-py3-none-any.whl \
  --expected-version 0.5.0 --upgrade-from-release 0.4.2 --acceptance-only --frameworks \
  --soak-seconds 30 --soak-shared-engine --report-dir build/v050-upgrade
uv run python scripts/validate_providers.py \
  --wheel dist/agent_runtime_kit-0.5.0-py3-none-any.whl \
  --expected-version 0.5.0 --report-dir build/v050-providers
uv run python scripts/validate_core.py --wheel-dir dist --report-dir build/v050-core
```

The upgrade harness downloads actual v0.4.2, verifies its pinned SHA-256, seeds its state and only
then installs the target wheel. It checks exact response/history replay, feedback, standalone
memory, unchanged owner/lease fields and continued invocation. All historical upgrade paths remain
required. Retain source checksum, installed provenance, dependency inventories and JUnit/soak reports.
After publication repeat acceptance against the downloaded asset, not a rebuilt approximation.

## Rollback

Drain workers before restoring the previous host lock and version. No schema downgrade is needed
from v0.5.0 to v0.4.2. Rollback restores weaker custom stream/result/tool validation and eager
adapter loading; it cannot retract delivered output, undo tool effects or erase settled run state.
Do not blindly retry failed keys. If the host changed its own payload/storage schema, review that
separately from the library's unchanged PostgreSQL revision.
