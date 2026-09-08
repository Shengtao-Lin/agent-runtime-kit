# Model Provider Runbook

## Supported adapters

| Provider | Import | `MODEL_PROVIDER` | Default endpoint |
| --- | --- | --- | --- |
| OpenAI-compatible Chat Completions | `OpenAICompatibleModelClient` | `openai_compatible` | `https://api.openai.com/v1` |
| Anthropic Messages | `AnthropicModelClient` | `anthropic` | `https://api.anthropic.com` |
| Google Gemini generateContent | `GeminiModelClient` | `gemini` | `https://generativelanguage.googleapis.com` |

Import adapters from their modules under `agent_runtime.models_clients`. Each implements
`generate()` and `stream()` and translates tool calls, usage, and messages into canonical types.

## Configuration

Set `MODEL_NAME` and `MODEL_API_KEY`; optionally set `MODEL_BASE_URL`. Anthropic also accepts
`MODEL_MAX_TOKENS` and `ANTHROPIC_API_VERSION`. Keep credentials in the deployment secret store,
never in `.env.example`, logs, traces, or images.

## Verify

```bash
uv run pytest tests/unit/test_openai_compatible.py tests/unit/test_anthropic.py tests/unit/test_gemini.py -q
```

The tests use HTTP mock transports and require no provider credentials. Before production use,
run a privacy-reviewed canary against the selected provider and model.

## Common failures

- Authentication errors: rotate or remount the secret; do not print it.
- Invalid tool calls: compare the registered JSON schema with the provider's supported subset.
- Empty output: inspect provider finish and safety metadata outside the public error response.
- Stream ends without `completed`: treat the call as failed and retry according to application
  policy; do not persist partial text as a successful assistant response.
- Compatible endpoint divergence: pin and test the endpoint because “OpenAI-compatible” does not
  guarantee identical streaming or tool behavior.

Official references: [OpenAI](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[Anthropic](https://docs.anthropic.com/en/api/messages), and
[Gemini](https://ai.google.dev/api/generate-content).
