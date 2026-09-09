# Telemetry Runbook

## Purpose and Ownership

The library emits OpenTelemetry spans for runtime, model, memory, tool, guardrail, and feedback
operations. Export is optional and must never make agent execution depend on collector
availability. The hosting application owns exporter configuration, access control, retention,
sampling, alerting, and provider shutdown.

## Prerequisites and Configuration

Install the `telemetry` extra when exporting traces:

```bash
uv add "agent-runtime-kit[telemetry] @ git+https://github.com/Shengtao-Lin/agent-runtime-kit@v0.1.0"
```

| Example setting | Default | Purpose |
| --- | --- | --- |
| `OTEL_SERVICE_NAME` | `agent-runtime-kit` | Service displayed in the trace backend |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | Full OTLP HTTP trace endpoint, including `/v1/traces` |
| `OTEL_CAPTURE_CONTENT` | `false` | Permit message content in telemetry |

An empty endpoint keeps tracing local/no-op for export. Leave content capture disabled unless data
classification, access, and retention have been reviewed.

## Application Integration

```python
from agent_runtime.telemetry import configure_telemetry

telemetry_handle = configure_telemetry(
    service_name="my-agent-service",
    endpoint="http://otel-collector:4318/v1/traces",
    capture_content=False,
)
telemetry = telemetry_handle.telemetry

# Pass telemetry to runtime, memory, feedback, tools, and native invokers.

try:
    await serve_application()
finally:
    telemetry_handle.shutdown()
```

Create one handle during application startup and flush it during graceful shutdown after in-flight
invocations complete.

## Local Verification

```bash
docker compose --profile observability up -d postgres otel-collector jaeger
uv run pytest tests/unit/test_telemetry.py tests/unit/test_telemetry_resilience.py -q
```

For a containerized app, use `http://otel-collector:4318/v1/traces`. Open Jaeger at
`http://127.0.0.1:16686`, run one synthetic invocation, and search for the configured service.

Expected span categories include runtime invocation plus the model, memory, tool, guardrail, and
feedback operations exercised by that request. Not every invocation contains every category.

## Operational Checks

- Search by run, request, and thread identifiers rather than message content.
- Confirm required span categories appear for a synthetic invocation.
- Check parent/child relationships and durations around provider, tool, and database boundaries.
- Monitor exporter failures separately from invocation success.
- Verify collector backpressure or outage does not fail the agent request.
- Flush the provider during controlled shutdown and check for dropped batches.
- Audit that content attributes are absent when capture is disabled.

## Troubleshooting

### No traces appear

Check the full endpoint including `/v1/traces`, container DNS, collector receiver configuration,
service name, and collector export logs. Generate a new synthetic request after configuration
changes; previously completed operations cannot be recreated.

### Traces work locally but not in containers

Do not use `127.0.0.1` from the app container to reach the collector container. Use the Compose
service name, confirm both services share a network, and inspect `docker compose logs
otel-collector`.

### Duplicate or fragmented traces

Confirm the application configures one provider, does not initialize telemetry once per request,
and passes the same `RuntimeTelemetry` instance across composed components.

### Exporter failure affects request behavior

This violates the telemetry boundary. Reproduce with the resilience tests and inspect custom
exporter or logging hooks. Agent execution must remain independent of collector availability.

### High trace volume

Apply deployment-level sampling and retention controls while preserving enough synthetic and error
traffic for diagnosis. Do not remove the stable run/request correlation attributes required for
operational use.

## Privacy Incident Response

If `OTEL_CAPTURE_CONTENT=true` was enabled unintentionally:

1. Disable it and redeploy or restart the affected application instances.
2. Restrict access to the collector and trace backend.
3. Identify affected services, trace IDs, and retention windows.
4. Follow the deployment's incident, deletion, and notification procedures.
5. Verify new synthetic spans contain no content attributes.

Rotating an API key does not remove content already exported to a telemetry backend.

## Data and Security Notes

The safe default records stable identifiers, counts, categorical status, and timing—not prompts,
outputs, tool arguments, memory values, free-text feedback, credentials, or database URLs. Trace
identifiers are still operational metadata and should be access-controlled.

## Related Documentation

- [User Runbook](../user-runbook.md)
- [Runtime](runtime.md)
- [Tools and Hooks](tools-and-hooks.md)
- [Architecture Decision 0003](../adr/0003-otel-content-capture.md)
