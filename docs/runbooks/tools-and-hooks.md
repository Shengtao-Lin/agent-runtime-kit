# Tools and Hooks Runbook

## Purpose and Ownership

`ToolRegistry` derives model-visible JSON schemas from Pydantic input models, validates arguments,
bounds execution, wraps synchronous handlers, and returns canonical `ToolResult` values.
`HookManager` runs ordered lifecycle policy at `before_model`, `after_model`, `before_tool`, and
`after_tool`.

Tools perform application actions. Hooks observe or block lifecycle events. Neither component owns
authentication, external transaction rollback, retry scheduling, or a production safety policy.

## Registration Workflow

```python
from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.tools import ToolRegistry


class LookupOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(pattern=r"^DEMO-[0-9]+$")


async def lookup_order(order_id: str) -> dict[str, str]:
    return {"order_id": order_id, "status": "shipped"}


tools = ToolRegistry(hooks=hook_manager, telemetry=telemetry)
tools.register(
    name="lookup_order",
    description="Look up one synthetic demonstration order.",
    input_model=LookupOrderInput,
    handler=lookup_order,
    timeout_seconds=5,
)
```

Registration fails for duplicate names or non-positive timeouts. Definitions are returned in
stable name order and can be passed directly to the native model loop.

## Hook Workflow

```python
from agent_runtime.hooks import HookManager

hooks = HookManager([audit_hook, policy_hook])
hooks.register(metrics_hook)
```

Hooks execute sequentially in registration order and stop at the first exception. Keep ordering
intentional: for example, a minimal audit hook may precede a blocking policy, while expensive work
should not run before an inexpensive rejection.

| Phase | Available focus | Typical use |
| --- | --- | --- |
| `before_model` | Runtime context and messages | Input policy, size limits, audit metadata |
| `after_model` | Runtime context and generated messages | Output policy and response checks |
| `before_tool` | Tool name, call ID, and arguments | Authorization and argument policy |
| `after_tool` | Tool name, call ID, and result | Result checks and categorical audit |

## Design Checklist

- Use stable, unique tool names and strict Pydantic models with bounded fields.
- Make handlers idempotent when the surrounding run can be retried.
- Set each tool deadline below the total invocation deadline.
- Validate authorization inside the application boundary before performing side effects.
- Propagate cancellation to downstream clients where supported.
- Return JSON-compatible, bounded results; do not return framework or database objects.
- Keep hooks deterministic, fast, and independently bounded when they call external policy systems.
- Treat `PhraseBlockGuardrail` and `MessageLengthGuardrail` as examples, not safety certification.

## Verification

```bash
uv run pytest tests/unit/test_tools.py tests/unit/test_hooks.py tests/unit/test_native.py -q
```

Add application tests for valid input, each validation boundary, synchronous and asynchronous
handlers, timeout, cancellation, authorization rejection, safe exception translation, hook order,
and model requests for unknown tools. Use synthetic inputs and mock downstream side effects.

Expected behavior:

- invalid arguments never reach the handler;
- synchronous handlers run off the event loop;
- a timed-out handler produces `tool_timeout`;
- successful results retain tool name and call ID;
- before/after hooks surround successful tool execution; and
- the native loop stops after its configured maximum tool iterations.

## Troubleshooting

### `invalid_tool_arguments`

Compare the provider-supplied arguments with the Pydantic schema returned by
`ToolRegistry.definitions()`. Correct the description, schema, or provider translation. Do not
coerce unexpected fields inside the handler.

### `tool_not_found`

Register the tool before publishing an agent that can request it. Check naming differences between
the model-visible definition and the application registry.

### `tool_timeout`

Cancel downstream work where supported and inspect dependency latency. Confirm a synchronous
handler is not spawning unmanaged background work. Increase the deadline only after measuring the
normal and tail latency.

### `tool_execution_failed`

Use protected internal exception chaining to locate the cause while keeping public messages safe.
Determine whether an external side effect completed before retrying; idempotency is the handler's
responsibility.

### `max_tool_iterations_exceeded`

Inspect the sequence of model tool calls and returned result shapes. Repeated requests often mean
the model cannot understand the result or the tool schema. Raising the iteration limit can amplify
cost and side effects.

### Hook latency dominates the request

Measure hooks separately, move non-blocking analytics out of the synchronous policy path, and put a
timeout around required external checks. Registration order is execution order.

## Security Notes

Tool execution is a privileged boundary. Validate identity and authorization in the hosting
application, scope downstream credentials, constrain inputs and outputs, and make high-impact
operations explicitly confirmable. Do not place secrets, raw tool arguments, results, prompts, or
memory values in logs or span attributes.

## Related Documentation

- [User Runbook](../user-runbook.md)
- [Runtime](runtime.md)
- [Model Providers](model-providers.md)
- [Telemetry](telemetry.md)
