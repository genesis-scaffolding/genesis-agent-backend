# Makefile for my-agent-backend
#
# Configuration:
#   MODELS_ROOT is read from .env (gitignored). Copy .env.example to .env
#   and set it to your models directory. Override per-call:
#     make ROOT=/path/to/models
#
# Targets:
#   make catalog [ROOT=/path]   regenerate MODEL_CATALOG.{yaml,md}
#   make config                 regenerate config.generated.yaml
#   make all [ROOT=/path]       catalog + config
#   make up                     start llama-swap (delegates to bin/up)

-include .env

# Resolution order: CLI flag > .env > unset (then catalog/all will error)
ROOT ?= $(MODELS_ROOT)

.PHONY: all catalog config help up

help:
	@echo "Targets:"
	@echo "  make catalog [ROOT=/path]   regenerate MODEL_CATALOG.{yaml,md}"
	@echo "  make config                 regenerate config.generated.yaml"
	@echo "  make all [ROOT=/path]       catalog + config"
	@echo "  make up                     start llama-swap (delegates to bin/up)"

catalog:
	@test -n "$(ROOT)" || { echo "error: MODELS_ROOT not set. Copy .env.example to .env and edit, or pass ROOT=/path" >&2; exit 1; }
	python3 bin/catalog.py "$(ROOT)"

config: config.generated.yaml

config.generated.yaml: recipes.yaml MODEL_CATALOG.yaml bin/build-config.py
	python3 bin/build-config.py

all: catalog config

up:
	./bin/up