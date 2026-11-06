# Stage F explicit framework model boundaries

`build/stage-f-frameworks-1` passed all 112 external pytest tests in 22.58 seconds
against SHA-verified actual v0.7.0 and PostgreSQL 16.15, on Windows/Python 3.12.10.
The independently locked framework versions are langchain-core 1.7.6 and LangGraph
1.2.20. Full dependency inventory and exact sources are retained in
[raw evidence](evidence/stage-f-framework-boundaries/raw.tar.gz) with
[checksums](evidence/stage-f-framework-boundaries/checksums.json).

## What is integrated

The host creates a real `RunnableLambda` or compiled `StateGraph` with model/tool
nodes. It explicitly wraps its offline canonical model delegate using the same
application module validated for native dispatch. A host mapper may add a large
system message immediately before invoking that delegate. Framework tool nodes
explicitly reuse ToolRegistry; framework execution and routing remain host-owned.
The adapter uses an explicit stream mapper for framework state values. Provisional
text is emitted from completed node values; this fixture does not claim token-by-token
forwarding of an arbitrary framework chat model's internal stream.

## Direct observations

Both frameworks exercise invoke and real incremental framework streaming, with
generate/stream delegates respectively. The five native growth cases and an extra
final-mapper growth case fail before oversized delegate dispatch. All admitted calls
fit both budgets. Hook/schema/mapper failures admit zero calls; accumulated tool
growth admits earlier valid calls and preserves their effects. Durable failed runs
have no successful history or response, and failed-key reuse performs no new work.

Normal two-round cases succeed with complete history and exact replay. Delegate
stream opening/closure counters balance, and every host leaves zero tagged database
sessions, pool checkouts or asynchronous tasks. Framework cancellation/deadline
tests also settle durable failure and close the waiting delegate, covering generate,
stream and generate-only use through each framework.

Entry-only negative controls deliberately omit the delegate wrapper. They pass the
initial 105-byte check, then dispatch oversized tool-result or mapper-expanded input
and succeed. Thus initial/runtime-entry validation cannot substantiate an internal
model-call guarantee. These negative cases are expected observations, not protective
acceptance.

## Boundaries of the conclusion

This proves explicit integration through the installed public adapters and model
protocols. It does not cover unobserved framework-native models, arbitrary vendor
serialization, automatic interception, framework-owned tool policy or production
model quality. No provider credentials or model network calls are used. The native
overhead report uses the earlier, separately frozen dependency inventory; framework
extras do not change the measured host policy implementation.
