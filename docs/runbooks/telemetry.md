# Telemetry Runbook

## Responsibility

The library emits OpenTelemetry spans for runtime, model, memory, tool, guardrail, and feedback
operations. Export is optional and must never make agent execution depend on collector availability.

## Local verification

```bash
docker compose --profile observability up -d postgres otel-collector jaeger
uv run pytest tests/unit/test_telemetry.py tests/unit/test_telemetry_resilience.py -q
```

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to the full OTLP HTTP traces endpoint and open Jaeger at
`http://127.0.0.1:16686`.

## Operational checks

- Search by run, request, and thread identifiers.
- Confirm all required span categories appear for a synthetic invocation.
- Monitor exporter failures separately from invocation success.
- Flush the provider during graceful shutdown.

## Privacy incident response

`OTEL_CAPTURE_CONTENT=false` is the safe default. If content was enabled unintentionally, disable
it, restrict collector access, identify affected retention windows, and follow the deployment's
incident and deletion procedures. Rotating an API key does not remove already exported content.
