# Makefile for my-agent-backend
#
# Configuration:
#   MODELS_ROOT is read from .env (gitignored). Copy .env.example to .env
#   and set it to your models directory. Override per-call:
#     make ROOT=/path/to/models
#
# Targets:
#   make catalog [ROOT=/path]   regenerate MODEL_CATALOG.{yaml,md}
#   make config                 regenerate config.yaml
#   make all [ROOT=/path]       catalog + config
#   make up                     start llama-swap (delegates to bin/up)
#   make install-model REPO=x   interactively select/download an HF model
#                                (DRY_RUN=1 previews; YES=1 skips confirmation)
#   make pi-print                print pi-agent models.json to stdout
#   make pi-models.json [BASE=u] write pi-models.json only
#   make pi-install [BASE=url]   write pi-models.json and copy to ~/.pi

-include .env

# Resolution order: CLI flag > .env > unset (then catalog/all will error)
ROOT ?= $(MODELS_ROOT)

.PHONY: all catalog config help up install-model pi-print pi-install pi-models.json

help:
	@echo "Targets:"
	@echo "  make catalog [ROOT=/path]   regenerate MODEL_CATALOG.{yaml,md}"
	@echo "  make config                 regenerate config.yaml"
	@echo "  make all [ROOT=/path]       catalog + config"
	@echo "  make up                     start llama-swap (delegates to bin/up)"
	@echo "  make install-model REPO=x  interactively select/download an HF model"
	@echo "                              (DRY_RUN=1 previews; YES=1 skips confirm)"
	@echo "  make pi-print              print pi-agent models.json to stdout"
	@echo "  make pi-models.json [BASE] write pi-models.json (no install)"
	@echo "  make pi-install [BASE=url] write pi-models.json and copy to ~/.pi"

catalog:
	@test -n "$(ROOT)" || { echo "error: MODELS_ROOT not set. Copy .env.example to .env and edit, or pass ROOT=/path" >&2; exit 1; }
	python3 bin/catalog.py "$(ROOT)"

config: config.yaml

config.yaml: recipes.yaml MODEL_CATALOG.yaml bin/build-config.py
	python3 bin/build-config.py

all: catalog config

up:
	./bin/up

install-model:
	@test -n "$(ROOT)" || { echo "error: MODELS_ROOT not set. Copy .env.example to .env and edit, or pass ROOT=/path" >&2; exit 1; }
	@test -n "$(REPO)" || { echo "usage: make install-model REPO=org/model [ROOT=/path]" >&2; exit 1; }
	./bin/hf-model.py $(if $(DRY_RUN),--dry-run,) $(if $(YES),--yes,) --root "$(ROOT)" "$(REPO)"
	if [ -z "$(DRY_RUN)" ]; then $(MAKE) all ROOT="$(ROOT)"; fi

pi-models.json: config.yaml bin/pi-models.py
	./bin/pi-models.py --output pi-models.json $(if $(BASE),--base-url $(BASE),)

pi-print: config.yaml bin/pi-models.py
	@./bin/pi-models.py --stdout $(if $(BASE),--base-url $(BASE),)

pi-install: pi-models.json
	install -m 0644 pi-models.json "$(HOME)/.pi/agent/models.json"
	@echo "installed to ~/.pi/agent/models.json — reload pi (or /model) to pick up changes"
