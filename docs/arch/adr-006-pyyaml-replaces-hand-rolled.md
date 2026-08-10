# ADR-006: PyYAML replaces hand-rolled YAML emit and parse

## Title
PyYAML replaces hand-rolled YAML emit and parse

## Context
Two `bin/` scripts hand-roll YAML emission to avoid a `PyYAML` dependency:

- `bin/catalog.py` emits `MODEL_CATALOG.{yaml,md}` via a custom `_yaml_scalar` / `_emit_entry` pair, with documented reasoning: "hand-rolled to avoid an external dep."
- `bin/build-config.py` also emits `config.yaml` by hand (with the same helper, copied) but imports `yaml` (PyYAML) only for **reading** `MODEL_CATALOG.yaml` and `recipes.yaml`.
- `bin/pi-models.py` hand-rolls YAML **reading** of `config.yaml` via regex (`parse_models_section`), again to avoid a PyYAML dependency in the PEP 723 inline-script environment.

This state is inconsistent and wasteful. Three scripts reinvent YAML at varying levels of correctness, with two divergent implementations of the same emitter.

The hand-rolling was justified when each `bin/` script was a self-contained, dependency-free CLI. The worker is becoming a proper `pyproject.toml`-based project. `PyYAML` will be a project dependency regardless (it is already needed for reading recipes). Hand-rolling no longer buys anything.

The cost of switching to PyYAML is byte-level formatting differences in the generated artifacts (`MODEL_CATALOG.yaml`, `config.yaml`, `pi-models.json`). Content must remain equivalent; formatting (key order, quote style, whitespace) may differ.

## Decision

We will use `PyYAML` for all YAML emit and parse inside the worker. Hand-rolled YAML in the `bin/` scripts is replaced when those scripts are ported into the new package; the `bin/` scripts themselves are retired post-v1 (ADR-008) and not modified during v1.

Specific replacements:

- `MODEL_CATALOG.yaml` emit: `yaml.dump(data, sort_keys=False, default_flow_style=False)` with a custom representer for `Path` objects.
- `MODEL_CATALOG.md` emit: unchanged (it's markdown).
- `config.yaml` emit: same `yaml.dump` with `default_flow_style=False` and explicit control over block style for the `cmd:` field (PyYAML's literal-block `|` is the right choice; configured via `default_style` or a representer).
- `config.yaml` parsing in `agent_export.py`: `yaml.safe_load` (replaces regex-based `parse_models_section`).
- `recipes.yaml` parsing: already PyYAML (`yaml.safe_load`); continues.
- `pi-models.json` emit: `json.dumps(..., indent=2, sort_keys=True)` (was already this).

Content equivalence is verified by Phase-validation diffs against the current artifacts. Byte equivalence is not required.

## Status
Accepted

## Consequences

Positive:
- One consistent YAML library across the codebase.
- Hand-rolled YAML bugs (escape edge cases, block-scalar handling) become PyYAML bugs we don't have to maintain.
- New code can rely on standard YAML semantics rather than the subset our hand-rollers handled.
- `bin/pi-models.py`'s brittle regex-based `parse_models_section` becomes a clean `yaml.safe_load`.

Negative:
- Generated artifacts (`MODEL_CATALOG.yaml`, `config.yaml`) will diff in formatting on first regen after the migration. Cosmetic; commit-noise is a one-time event.
- `PyYAML` is now a hard project dependency. Already was, transitively (reading recipes). Not a regression.

Neutral:
- We accept that pydantic models and `yaml.dump` interop requires care for `Path` / `datetime` / enum fields. Mitigated by custom representers.

## Spec
[spec-001-core-architecture](specs/spec-001-core-architecture.md)

## Plan
[plan-001-core-architecture](plans/plan-001-core-architecture.md)
