FROM ghcr.io/astral-sh/uv:0.12.10 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev \
    --extra postgres \
    --extra service \
    --extra telemetry \
    --extra adapters \
    --extra openai-compatible \
    --extra anthropic \
    --extra gemini

FROM python:3.12-slim AS runtime
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd --system agent-runtime \
    && useradd --system --gid agent-runtime --home-dir /app agent-runtime
WORKDIR /app
COPY --from=builder --chown=agent-runtime:agent-runtime /app/.venv ./.venv
COPY --chown=agent-runtime:agent-runtime src ./src
COPY --chown=agent-runtime:agent-runtime examples ./examples
COPY --chown=agent-runtime:agent-runtime alembic ./alembic
COPY --chown=agent-runtime:agent-runtime alembic.ini ./alembic.ini
COPY --chown=agent-runtime:agent-runtime infra ./infra
USER agent-runtime
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
ENTRYPOINT ["python", "-m"]
CMD ["examples.support_agent.main", "--host", "0.0.0.0", "--port", "8000", "--log-config", "/app/infra/logging.json"]
