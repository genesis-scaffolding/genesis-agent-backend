# Makefile for the Genesis Worker (`genesis_worker` package).
#
# Spec: docs/arch/specs/spec-017-makefile-rewrite.md

.DEFAULT_GOAL := help

.PHONY: help install test test-fast lint typecheck ui env-init build clean

help:
	@echo "Targets:"
	@echo "  make install      uv sync"
	@echo "  make test         pytest + pyright + ruff (the gate)"
	@echo "  make test-fast    pytest only"
	@echo "  make lint         ruff check genesis_worker"
	@echo "  make typecheck    pyright"
	@echo "  make ui           launch the Streamlit UI (genesis-worker-ui)"
	@echo "  make env-init     create .env from .env.example if absent"
	@echo "  make build        uv build (wheel + sdist under dist/)"
	@echo "  make clean        remove build and cache artifacts"

install:
	uv sync

test:
	uv run pytest -q
	uv run pyright
	uv run ruff check genesis_worker

test-fast:
	uv run pytest -q

lint:
	uv run ruff check genesis_worker

typecheck:
	uv run pyright

ui:
	uv run genesis-worker-ui

env-init:
	@if [ -f .env ]; then \
		echo ".env already exists; leaving it alone."; \
	else \
		cp .env.example .env && echo "Created .env from .env.example — edit it before running the worker."; \
	fi

build:
	uv build

clean:
	rm -rf dist/ .pytest_cache/ .ruff_cache/
	find . -path './.venv' -prune -o -path './.git' -prune -o -type d -name __pycache__ -print0 | xargs -0 -r rm -rf
