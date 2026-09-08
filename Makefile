.PHONY: install postgres-up migrate example docker-build docker-up docker-smoke format lint typecheck test test-unit test-integration verify

TEST_DATABASE_URL ?= postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime

install:
	uv sync --all-extras --dev

postgres-up:
	docker compose up -d postgres

migrate:
	uv run alembic upgrade head

example:
	uv run python -m examples.support_agent.main --fake-model

docker-build:
	docker compose build app

docker-up:
	docker compose up -d app postgres

docker-smoke:
	python scripts/docker_smoke.py

format:
	uv run ruff format .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run pyright

test:
	AGENT_RUNTIME_TEST_DATABASE_URL=$(TEST_DATABASE_URL) uv run pytest -q

test-unit:
	uv run pytest -m "not integration" -q

test-integration:
	AGENT_RUNTIME_TEST_DATABASE_URL=$(TEST_DATABASE_URL) uv run pytest -m integration -q

verify: lint typecheck test
