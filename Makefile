.PHONY: install postgres-up migrate format lint typecheck test verify

install:
	uv sync --all-extras --dev

postgres-up:
	docker compose up -d postgres

migrate:
	uv run alembic upgrade head

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
