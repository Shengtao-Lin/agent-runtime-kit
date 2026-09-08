from agent_runtime.telemetry import configure_telemetry


def test_unavailable_collector_does_not_break_runtime_spans() -> None:
    handle = configure_telemetry(
        service_name="telemetry-resilience-test",
        endpoint="http://127.0.0.1:9/v1/traces",
    )
    with handle.telemetry.span("agent.runtime.run", {"agent.id": "test"}):
        pass
    handle.shutdown()
