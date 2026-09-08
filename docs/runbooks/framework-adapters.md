# Framework Adapter Runbook

## Responsibility

Adapters translate canonical `InvocationInput` and `InvocationOutput` at the framework edge.
`NativeAgentInvoker` owns the model/tool loop. `LangChainRunnableAdapter` and `LangGraphAdapter`
wrap public framework objects with explicit mapper functions.

## Verify

```bash
uv run pytest tests/unit/test_adapters.py tests/unit/test_native.py -q
```

## Integration checklist

- Register one stable `agent_id`, semantic version, framework name, and capability set.
- Supply explicit mappers when the wrapped runnable or graph does not use canonical dictionaries.
- Return only newly generated messages; the runtime already supplied prior PostgreSQL history.
- Do not configure persistent LangChain memory or a persistent LangGraph checkpointer.
- Implement the optional `StreamingAgentInvoker` protocol for true incremental output. Non-streaming
  invokers still work and produce `started` followed by `completed`.

## Common failures

- Mapping failure: capture the framework output shape in a synthetic unit test, without customer
  data, then correct the boundary mapper.
- Duplicate agent: change the registration configuration rather than replacing entries at runtime.
- History appears twice: remove framework-owned persistent memory or stop returning input history.
- Cancellation leaks work: ensure the wrapped framework propagates async cancellation.
