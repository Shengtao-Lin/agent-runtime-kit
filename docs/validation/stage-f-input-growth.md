# Stage F unprotected input-growth baseline

The actual v0.7.0 wheel admits inputs beyond the frozen host budgets after initial
history selection. This is a limitation of initial context selection, not a
regression in its documented contract. The unchanged Release Desk baseline still
has zero overflows; see [its report](stage-f-release-desk-baseline.md).

## Reproduction

`scripts/validate_model_inputs.py` installs the SHA-verified published wheel in
an external venv and uses real PostgreSQL. Five focused host fixtures run through
both native invoke and stream, alongside the two unchanged 60-review host cases.
The acceptance limits remain those frozen in [the workload](stage-f-workload.md).

| Fixture | Actual messages per model call, bytes | Tool definitions, bytes |
| --- | --- | --- |
| One 256 KiB tool result | 105, 262609 | 236 |
| Three 24 KiB results | 105, 74690 | 236 |
| Three tool rounds | 105, 25040, 49975, 74910 | 236 |
| Hook adds three valid text blocks | 72183 | 236 |
| Large tool schema | 105 | 9261 |

The selected initial input is 105 bytes in every fixture. The actual model
receives the oversized input and completes successfully without a dispatch guard.
Invoke and stream produce identical counts. All complete histories persist,
successful responses replay exactly without model/tool work, opened streams
close, and no checked-out connections, PostgreSQL sessions or asynchronous tasks
survive host teardown. Tool effects remain observable.

## Evidence and qualification

`build/stage-f-v070-growth-2` passed all 12 tests in 6.54 seconds on Windows,
Python 3.12.10 and PostgreSQL 16.15. The first attempt passed 10 and failed two:
its hook tried to construct one text block longer than the existing 32000-character
canonical limit. Splitting the fixture into three legal 24000-character blocks
reproduced aggregate growth without weakening canonical validation. Both attempts,
their manifests, exact measured sources, package provenance and safety samples
are preserved in [the evidence archive](evidence/stage-f-v070-growth/raw.tar.gz),
with [checksums](evidence/stage-f-v070-growth/checksums.json).

Passing this baseline means the observations and persistence checks hold. It
does **not** mean the budgets were enforced. These are deterministic regressions
in the existing published-model-input CI job, explicitly expecting unprotected
violations until a separately tested opt-in host guard is attached.

## Next decision

Evaluate an explicit host `ModelClient` wrapper before modifying the library.
It can observe each native dispatch after hooks and tool results and preserve
the existing generate/optional-stream contracts. A native-invoker-only guard
would not protect an application's LangChain or LangGraph model boundary.
An entry hook alone already fails the tool-growth cases. Framework integration
must place a check after its input mapper, immediately before each protected
model call. These are candidate designs; adoption requires boundary, lifecycle,
durability, independent-host and frozen overhead acceptance evidence.
