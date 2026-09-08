.PHONY: install postgres-up migrate example docker-build docker-up docker-smoke format lint typecheck test verify

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
	uv run pytest -q

verify: lint typecheck test
