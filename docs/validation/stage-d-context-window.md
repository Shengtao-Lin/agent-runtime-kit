# Stage D: Context Window Candidate Acceptance

Validated on 2026-11-05 (America/New_York). The optional window resolves the measured initial-context
gap while preserving full durable history. This report covers development code and an independently
installed candidate wheel; publication and Release Desk adoption remain separate delivery gates.
See [ADR 0015](../adr/0015-opt-in-initial-context-window.md) and the
[component runbook](../runbooks/context-window.md).

## Reproducible evidence

The [original baseline](stage-d-baseline.md) remains intact. After adding selection observations and
an RSS sampler failure guard, the final shared workload was run sequentially against:

- Actual published v0.5.0, wheel SHA-256
  `d112163a318aa280e64bd20a0d8dd1db519007db3fc5aa419f0f3d1bdb60cd59`, default full context.
- Development candidate, wheel SHA-256
  `3b6f4c68eed835bf339d7bf37af0eec106eebc007c913b0bbf79253df578fbd5`, explicit
  `ContextWindow(max_bytes=65536)`. Its pre-release-preparation package metadata is still `0.5.0`;
  it is a distinct local artifact and does not replace or describe the published v0.5.0 wheel.

Each run completed three repetitions of all fifteen profiles, or 45 fresh processes and 972 measured
continuations. Windows 11/Python 3.12.10, PostgreSQL 16 configuration, the workload source hashes,
resolved dependencies, effective history sizes and sample counts match. The comparator rejects
mismatches, incomplete experiments and smoke-only evidence. Independent controls passed four pytest
cases in each run. No time/RSS guard was hit or relaxed.

Retained evidence:

- [Comparison](evidence/stage-d-context-comparison/comparison.json),
  [baseline summary](evidence/stage-d-context-comparison/baseline-summary.json) and
  [candidate summary](evidence/stage-d-context-comparison/candidate-summary.json).
- [Baseline raw archive](evidence/stage-d-context-comparison/baseline-raw.tar.gz) and
  [candidate raw archive](evidence/stage-d-context-comparison/candidate-raw.tar.gz): raw profiles,
  source manifest, package audit and control-test JUnit. Summaries retain each raw JSON SHA-256.
- [Archive/summary checksums](evidence/stage-d-context-comparison/checksums.json). Archives preserve
  original bytes; checked-in evidence JSON disables Git newline conversion.

Use `scripts/validate_workload.py`, `scripts/summarize_workload.py` and
`scripts/compare_workloads.py` as described in the [workload README](../../validation/workloads/README.md).
Extract archives into separate new directories to rerun the summarizer/comparator without repeating
database execution. Source hashes describe the measured files, including their original line endings.

## Result and tradeoffs

All 972 candidate initial inputs fit the 65,536-byte allowance; their maximum is 65,479 bytes.
The matched baseline exceeds that allowance in 510/972 samples. The complete stored history and
effective read sizes remain equal between the two runs; selection does not delete durable messages.

| Initial turns / conversations / padding / mode | Maximum initial bytes, baseline -> candidate | End-to-end p95 ms, baseline -> candidate | Maximum measured RSS MiB, baseline -> candidate |
| --- | --- | --- | --- |
| 10 / 1 / 1 KiB / invoke | 79,068 -> 65,479 | 62.5 -> 87.6 | 77.4 -> 77.7 |
| 100 / 1 / 1 KiB / invoke | 323,598 -> 65,479 | 136.6 -> 151.9 | 82.8 -> 82.7 |
| 1,000 / 1 / 1 KiB / invoke | 2,768,898 -> 65,479 | 1,173.3 -> 851.2 | 130.3 -> 129.7 |
| 1,000 / 1 / 1 KiB / stream | 2,768,898 -> 65,479 | 1,269.7 -> 793.5 | 130.0 -> 129.8 |
| 10 / 4 / 1 KiB / invoke | 38,253 -> 38,253 | 159.9 -> 187.7 | 78.2 -> 79.4 |
| 10 / 16 / 1 KiB / invoke | 30,090 -> 30,090 | 625.2 -> 858.3 | 78.3 -> 78.5 |
| 10 / 1 / 16 KiB / invoke | 524,508 -> 54,438 | 160.4 -> 156.3 | 79.8 -> 79.4 |
| 10 / 1 / 256 KiB / invoke | 7,651,548 -> 195 | 209.6 -> 166.1 | 97.2 -> 95.9 |
| 10 / 4 / 1 KiB / slow stream | 38,253 -> 38,253 | 290.6 -> 289.7 | 78.1 -> 78.7 |

Selection adds serialization, relationship validation and snapshot cost even when the full context
already fits. Several short-history profiles are slower. This is accepted as an explicit optional
correctness/operating-boundary tradeoff, not a general speed optimization. The default path remains
unchanged. There is no new performance threshold or claim that every workload becomes faster.

At 1,000 initial turns in stream mode, both runs read up to 4,076 messages and 2,768,704 canonical JSON
bytes. Python allocation peaks are about 24.19 MB in both runs; RSS is about 130 MiB. First-text p95
from submission is 1,219.1 ms baseline versus 748.9 ms candidate, but measurement serialization p95
is 699.2 versus 164.8 ms. Reduced instrumentation work contributes materially: these results do not
establish uninstrumented speedup. Experiments are sequential, not randomized, on a shared local host;
database caches are not cleared and synthetic rows accumulate in isolated namespaces. Raw timings
are descriptive observations, not production SLOs, confidence intervals or throughput certification.

The native second model call can still exceed the initial allowance: maximum candidate model input
is 67,519 bytes with 1 KiB padding, 71,838 with 16 KiB, and 263,355 with 256 KiB. Current tool results
remain whole. The large-result profile omits all optional historical turns and passes a 195-byte
current request at entry; it does not truncate the ensuing tool result. This validates the documented
separation between initial context and per-model/token budgets.

## Correctness and integration acceptance

Local venv pytest passed all **681** root tests, including **19** selector cases and **11** real
PostgreSQL context cases. The independent installed-wheel consumer passed **111**, with one
upgrade-only skip and four readiness probes deselected. Those exclusions are not successful upgrade
or readiness results. Core-only installed-wheel import validation passed all twenty blocked prefixes.
Ruff, strict Pyright, dependency-direction and documentation checks passed.

Coverage includes exact UTF-8 byte boundaries; multiple current messages; historical system ordering;
parallel/cross-request tool relationships; malformed exchanges; detached nested payloads; explicit
mandatory overflow; default compatibility; selected hook observations; full durable transcripts;
safe failed-run codes with no dispatch; downstream failure/cancellation; same-thread exclusion;
128-event slow-consumer/early-close behavior; policy changes across runtime recreation; exact replay
from a separate Python process with an impossible new budget; and pool/resource cleanup. Real
LangChain and LangGraph consumers verify selected canonical entry, toolkit tool execution, durable
history, replay and cancellation. Their internal mapper/checkpoint/model budget remains host-owned.

The CI candidate workload smoke now opts into the window while the actual v0.5.0 smoke keeps default
behavior. Consumer and lower/latest compatibility lanes include selector, persistence and real
framework context acceptance. Existing upgrade, provider, streaming and ownership gates remain.
Final-head PR/main checks and the subsequent minor release's actual assets/adoption are still required.
