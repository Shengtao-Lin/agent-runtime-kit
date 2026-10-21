# Fictional Support Agent

This example exposes native, LangChain, and LangGraph implementations through the same canonical
HTTP contract. It uses only synthetic `DEMO-*` data. PostgreSQL remains the sole owner of durable
conversation history; the framework adapters do not configure memory or a checkpointer.

All three implementations execute the same model and typed tools. The LangChain runnable owns
its bounded loop; the compiled LangGraph graph owns model/tool routing. They use native framework
messages and return only newly generated messages, tool results, and usage. LangChain/LangGraph
streaming intentionally uses `started`/`completed` fallback, not synthetic token deltas.

```bash
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m examples.support_agent.main --fake-model
```

The default fake-model mode requires no API key. Set `FAKE_MODEL=false`, `MODEL_NAME`,
`MODEL_API_KEY`, and optionally `MODEL_BASE_URL` for a real provider. Choose
`MODEL_PROVIDER=openai_compatible`, `anthropic`, or `gemini`.

This directory is an integration example, not part of the Python wheel. Applications should import
and compose `agent_runtime` components directly.
