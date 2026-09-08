# Tools and Hooks Runbook

## Responsibility

`ToolRegistry` validates JSON-schema-compatible arguments, bounds execution, wraps sync handlers,
and returns canonical `ToolResult` objects. `HookManager` executes ordered lifecycle hooks at
`before_model`, `after_model`, `before_tool`, and `after_tool`.

## Verify

```bash
uv run pytest tests/unit/test_tools.py tests/unit/test_hooks.py tests/unit/test_native.py -q
```

## Registration checklist

- Use a stable unique tool name and a strict Pydantic input model.
- Keep handlers idempotent when callers may retry the surrounding run.
- Set a deadline shorter than the total invocation deadline.
- Do not place secrets, raw arguments, or outputs in logs and span attributes.
- Treat the included phrase and length guardrails as demonstrations, not a safety certification.

## Common failures

- `invalid_tool_arguments`: correct the model schema or provider tool translation.
- `tool_not_found`: register the capability before exposing an agent that can request it.
- `tool_timeout`: cancel downstream work where supported and inspect dependency latency.
- `tool_execution_failed`: use internal exception chaining for diagnosis; keep public errors safe.
- `max_tool_iterations_exceeded`: inspect repeated provider calls before raising the limit.

Hook order is registration order. Keep hooks deterministic and fast; expensive policy calls need an
explicit timeout inside the hook implementation.
