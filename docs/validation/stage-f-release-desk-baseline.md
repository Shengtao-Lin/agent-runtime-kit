# Stage F: Actual v0.7.0 Release Desk Baseline

Date: 2026-11-06. This is the first normal-host baseline, not complete Stage F acceptance or a
decision to add a library API. Requirements were frozen in `51c3719` before measurement.

## Provenance and execution

The external venv installed actual v0.7.0 from its independently locked GitHub wheel URL, SHA-256
`17464f89c4516b9fc3ef706db54c7b69ea5de4d6dfc67049f650d449464b39cd`. A separate bounded download
verified that digest, and all sixty installed package-file bytes matched it. The host source is
Release Desk from main `4b896d7708431e90013f482ec2a82777c671f730`, with explicit incremental reads
and a 65,536-byte initial window. No library or application business code was changed for the probe.

The runner at `815f64a` copied exact measured sources into the report before execution. Its subsequent
import-string line-length cleanup does not relabel those recorded bytes. The independent validation
lock records Windows Python 3.12.10, PostgreSQL 16.15, SQLAlchemy 2.1.8, Psycopg 3.3.8 and Pydantic
2.14.3. Pytest ran outside the checkout against a new `ark-stage-f-validation` database/volume.

The initial run stopped before migrations/tests because the verifier incorrectly assumed uv's
valid PEP 610 `archive_info` always contained hashes. Inspection showed an empty dictionary. The
corrected verifier checks the locked source URL and actual installed bytes against the verified
download instead. The failed manifest and measured sources remain separate from successful evidence.

## Observed normal-host result

| Mode | Reviews | Observed model calls | Calls above 65,536 message bytes | Largest message input |
| --- | ---: | ---: | ---: | ---: |
| Invoke | 60 | 120 | 0 | 65,190 bytes |
| Response stream | 60 | 120 | 0 | 65,190 bytes |

Both pytest cases pass. Each conversation retains all 240 messages; exact replay makes no new
model/tool calls. Tool definitions stay below the separately frozen 8,192-byte allowance. Pool
checkouts, tagged database sessions and surviving host tasks are zero after teardown. RSS safety
samplers stop cleanly and remain below the one-GiB experiment ceiling.

Instrumentation observes each deterministic router generation, including the generation delegated
by its streaming method. Counts describe complete canonical inputs; they are not vendor request
bytes or model tokens. These runs do not measure guard overhead or establish a latency/RSS SLO.

**No input-budget gap was reproduced in the unchanged normal Release Desk fixture.** Its fixed
small tool report and whole-history-group selection leave enough room for the next model call in
these sixty-turn conversations. Do not change this result or the budget to manufacture a violation.
It does not prove safety for large tool results, multiple tools/rounds or input-hook mutation.

## Remaining Stage F work

Execute the already specified focused growth, schema and hook scenarios, then compare host wrapping
with native integration and explicit model-boundary policy. Retain no-change/default compatibility
controls. Real-framework placement, safe durable rejection, effect/replay/stream ownership and three
matched overhead repetitions remain required before choosing or delivering the final solution.

Retain [raw reports and exact measured sources](evidence/stage-f-v070-release-desk/raw.tar.gz) and
[checksums](evidence/stage-f-v070-release-desk/checksums.json). The original local reports are
`build/stage-f-v070-baseline-1` (pre-test verifier failure) and `build/stage-f-v070-baseline-2`
(successful normal-host measurement). The Stage F service remains owned by the active goal and
must be stopped with evidence preserved at completion.

## Frozen-host harness confirmation

To keep future application adoption separate, `42d4ed5` vendors the four host source files with
their original commit and hashes under `validation/model_inputs/release_desk_source`. The runner
verifies those hashes before copying into the external consumer. A repeat in
`build/stage-f-v070-baseline-3` passed both pytest cases with the same 240 calls, zero overflows and
65,190-byte maximum. This confirms the changed source-loading boundary; it is not a three-repeat
performance comparison. [Confirmation evidence](evidence/stage-f-v070-frozen-host/raw.tar.gz) and
[checksum](evidence/stage-f-v070-frozen-host/checksums.json) retain that run separately.
CI now runs the frozen actual-wheel/host probe with real PostgreSQL and uploads its raw reports.
