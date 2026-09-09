# Model Provider Runbook

## Purpose and Ownership

Provider adapters translate canonical messages, tool definitions, tool calls, usage, and streaming
events at the external model API boundary. They do not own retries, durable history, run state,
application authorization, or provider account configuration.

## Supported Adapters

| Provider | Import | Extra | Example selector | Default base URL |
| --- | --- | --- | --- | --- |
| OpenAI-compatible Chat Completions | `OpenAICompatibleModelClient` | `openai-compatible` | `openai_compatible` | `https://api.openai.com/v1` |
| Anthropic Messages | `AnthropicModelClient` | `anthropic` | `anthropic` | `https://api.anthropic.com` |
| Google Gemini generateContent | `GeminiModelClient` | `gemini` | `gemini` | `https://generativelanguage.googleapis.com` |

Import clients from their modules under `agent_runtime.models_clients`. Each provides `generate()`
and `stream()` and uses direct HTTP so the core package does not impose a vendor SDK.

## Prerequisites and Installation

Install only the adapter required by the application:

```bash
uv add "agent-runtime-kit[openai-compatible] @ git+https://github.com/Shengtao-Lin/agent-runtime-kit@v0.1.0"
```

Replace the extra with `anthropic` or `gemini` as needed. Provision a provider credential through a
secret manager and confirm outbound HTTPS access to the selected endpoint.

## Configuration

| Value | Required | Notes |
| --- | --- | --- |
| Model name | Yes | Use an exact provider model identifier available to the account |
| API key | Yes | Never place it in source, traces, logs, or container layers |
| Base URL | No | Set only for an approved proxy or compatible endpoint |
| Request timeout | Recommended | Must fit inside the total runtime invocation deadline |
| Maximum output tokens | Anthropic | Defaults to `1024` in the example |
| API version | Anthropic | Defaults to `2023-06-01` in the example |

The example accepts these values through `MODEL_PROVIDER`, `MODEL_NAME`, `MODEL_API_KEY`,
`MODEL_BASE_URL`, `MODEL_MAX_TOKENS`, and `ANTHROPIC_API_VERSION`.

## Library Integration

```python
from agent_runtime.models_clients.openai_compatible import OpenAICompatibleModelClient

model_client = OpenAICompatibleModelClient(
    model="provider-model-id",
    api_key=api_key,
    base_url="https://api.openai.com/v1",
    timeout_seconds=45,
)
```

Equivalent constructors are available for Anthropic and Gemini. Pass the client to
`NativeAgentInvoker`, and close it during application shutdown:

```python
try:
    response = await runtime.invoke("my-agent", request)
finally:
    await model_client.close()
```

The application should keep a long-lived client for normal traffic and close it once at shutdown;
the snippet illustrates lifecycle ownership, not per-request client creation.

## Promotion Workflow

1. Run unit tests; they use mocked HTTP transports and require no credentials.
2. Run one privacy-reviewed non-streaming canary with no tools.
3. Verify output and usage mapping.
4. Run a streaming canary and require a terminal completion.
5. Add one synthetic tool call and validate argument/result translation.
6. Exercise authentication, provider error, safety stop, and timeout paths.
7. Pin the model and endpoint configuration before increasing traffic.

```bash
uv run pytest tests/unit/test_openai_compatible.py tests/unit/test_anthropic.py tests/unit/test_gemini.py -q
```

## Expected Behavior

- `generate()` returns one canonical completed model result.
- `stream()` yields text deltas and exactly one completed model result.
- Tool calls retain stable call IDs, names, and JSON-compatible arguments.
- Provider response details are translated to safe runtime exceptions at the boundary.
- Usage is populated when supplied by the provider; absent usage does not invent values.

## Troubleshooting

### Authentication or permission failure

Verify the secret is mounted, active, and authorized for the model and endpoint. Rotate or remount
the secret without printing it. A successful fake-mode request proves runtime composition, not
provider access.

### Model or endpoint not found

Confirm the exact model identifier, account region, proxy path, and base URL. Base URLs should not
include an endpoint path that the adapter already appends.

### Invalid or rejected tool call

Compare the Pydantic-derived tool schema with the provider's supported JSON Schema subset. Reduce
unsupported schema features or correct the provider translation; do not execute unvalidated
arguments.

### Empty output or safety stop

Inspect finish and safety metadata in protected internal diagnostics. Keep the public response
categorical and avoid logging prompts or provider bodies.

### Stream ends without completion

Treat the invocation as failed. Do not convert accumulated deltas into a successful assistant
message. Retry according to runtime idempotency policy after checking the durable run.

### OpenAI-compatible endpoint behaves differently

Compatibility does not guarantee identical streaming frames, tool-call deltas, error bodies, or
usage fields. Pin the endpoint version, add mock fixtures for its actual protocol, and complete the
same canary workflow used for a first-party provider.

## Security and Cost Notes

Provider prompts and outputs may leave the application's trust boundary. Review data residency,
retention, safety, and contract terms before sending production data. Apply application-level rate
and token limits, monitor usage externally, and redact credentials from all failures. The adapter
does not choose models or implement cost budgets.

## Related Documentation

- [User Runbook](../user-runbook.md)
- [Runtime](runtime.md)
- [Tools and Hooks](tools-and-hooks.md)
- Official references: [OpenAI](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create), [Anthropic](https://docs.anthropic.com/en/api/messages), and [Gemini](https://ai.google.dev/api/generate-content)
