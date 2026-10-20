.PHONY: test lint run-api

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

run-api:
	uv run uvicorn radar.api.main:app --port 8000
