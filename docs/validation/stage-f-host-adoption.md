# Stage F Release Desk adoption

The independent application now explicitly wraps its router with the host-owned
model input policy. The library stays at the actual published v0.7.0, with unchanged
public API, canonical contract `v1`, schema `0002` and independently pinned wheel
SHA-256 `17464f89c4516b9fc3ef706db54c7b69ea5de4d6dfc67049f650d449464b39cd`.
No new library release is necessary for this host-only change.

## Application acceptance

`scripts/validate_release_desk.py --live` installs the application outside the checkout
using its own locked venv and actual wheel. The application does not install LangChain
or the optional HTTP service. Real PostgreSQL 16.15 is used.

- `build/stage-f-adoption-1`: 23 passed in 12.59 seconds and public canary passed.
- `build/stage-f-adoption-2`: 23 passed in 12.51 seconds and public canary passed,
  adding exact source/lock/runner hashes and copies before execution. A report
  directory containing a source manifest can no longer be silently overwritten.

The original fourteen application cases remain. New cases verify both configuration
dimensions, literal `none` disable behavior, zero delegate calls for initial/schema
overflow, one admitted call and retained metadata read before post-tool rejection,
empty successful history for failed turns and failed-key reuse without work.
All sixty-review variants now observe all 120 actual calls, not only initial input.
They retain all 240 history messages and replay exactly with stricter model budgets.

The public read-only canary reviews `Shengtao-Lin/agent-runtime-kit` v0.7.0, passes
six metadata checks, then starts another CLI process with both model budgets lowered
to two bytes, the initial context window lowered to two and history streaming disabled.
Replay remains identical, history contains four messages, and repeated feedback is
idempotent. This is a public metadata/adoption canary, not model-quality or release
security evidence. No provider credentials or GitHub writes are used.

Raw reports, dependency inventory, provenance and the second run's pre-execution
source snapshot are retained in [the archive](evidence/stage-f-host-adoption/raw.tar.gz)
with [checksums](evidence/stage-f-host-adoption/checksums.json). The application lock
has its own Pydantic/SQLAlchemy/Psycopg versions; those are intentionally distinct
from the framework-validation lock and recorded rather than described as identical.

## Configuration and promotion

Default host composition enables 65536 canonical message bytes and 8192 tool-definition
bytes per model call. `RELEASE_DESK_MODEL_INPUT_MAX_BYTES=none` disables both checks;
initial context and history reading controls remain independent. Saved successes
replay unchanged; failed runs remain failed. See the
[runbook](../runbooks/model-input-budgets.md) and [adoption guide](../adopting-model-input-budgets.md).

Native and real-framework acceptance remains in the separate Stage F reports.
Exact final-head CI/application checks and applicable resulting-main checks gate
promotion of this adoption revision. This local acceptance alone is not a claim
that all delivery gates have completed.
