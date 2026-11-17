# Plan 048: Declared service ports, role labels and canonical names

**Repo:** `genesis-agent-backend` (worktree `worktrees/t-7aiamr-service-registry/genesis-agent-backend`,
branch `se/t-7aiamr-service-registry`, from `origin/main` @ `5630530`)
**Task:** `t-7aiamr-service-registry` — *canonical service-name registry in the framework: auto-set
ingress domain overrides for cluster-wide services* (the worker half of a cross-repo task).
**ADR:** `docs/arch/adr-049-declared-service-ports-and-canonical-names.md`
**Status:** implemented — the plan below is the execution reference for ADR-049.

The orchestrator half (the canonical-name registry, the per-port ingress routes) lives in
`genesis-infrastructure-toolkit` (ADR-021 / plan-020). This plan covers the worker half only:
the declaration, the contract, the read API and the allocator.

---

## 1. Goal

A declarative service spec declares:

- the **ports** it publishes — each with a role label, exactly one of them primary;
- the **canonical name** it answers to — the cluster-wide, node-independent label.

The worker reports both on `GET /v1/services`; the orchestrator turns them into hostnames and
routes. The worker never composes a hostname: the base domain belongs to the orchestrator.

## 2. The declaration

```yaml
canonical_name: genesis

ports:
  - label: ui
    primary: true
    host: $options.listen_port
    container: 3000
  - label: api
    host: $options.api_port
    container: 8765
```

`ports:` is the single source of truth when it is present — the legacy per-kind port fields must
be absent. The loader derives the runtime port fields from the block, so no runner changes.

A spec **without** a `ports:` block keeps today's shape: one implicit port, no role label. The
twenty shipped specs need no edit and their derived hostnames do not change.

## 3. File-by-file

| File | Change |
|---|---|
| `genesis_worker/contracts/service.py` | `ServicePort` (label / primary / endpoint); `InferenceService.canonical_name()`, `.service_ports()`, `.declared_host_ports()` — all defaulted, so a Python plugin needs no change. |
| `genesis_worker/contracts/__init__.py` | export `ServicePort`. |
| `genesis_worker/utils/services/spec.py` | `PortSpec`; `canonical_name` + `ports` on the three spec models; the cross-field validators (the eight rules); `ContainerSpec.listen_port` and the uv / compose `listen_port` become optional so "absent" is distinguishable from a value. |
| `genesis_worker/utils/services/base.py` | `DeclaredPort`; `DeclarativeServiceConfig` gains `canonical_name`, `ports`, `fixed_host_ports`; `DeclarativeServiceBase` implements the three contract members (the endpoint of a marker-backed port is resolved through the existing `PortBinder.resolve()`). |
| `genesis_worker/utils/services/loader.py` | collect the `$auto_port` use sites inside `ports[i].host`; derive the runtime port fields per kind; the compose template ↔ declared ports check. |
| `genesis_worker/utils/services/{docker_service,compose_service,uv_service}.py` | the config dataclasses inherit the three new fields from the base config. |
| `genesis_worker/utils/models.py` | `ServiceInfo` gains `canonical_name` and `ports`. |
| `genesis_worker/facade.py` | `list_services()` / `list_enabled_services()` populate both. |
| `genesis_worker/api/schemas.py` | `ServicePortSchema`; `ServiceSummarySchema` gains `canonical_name` and `ports` (additive). |
| `genesis_worker/registries.py` | `_reserved_ports()` asks each service for **all** its fixed host ports. |
| `genesis_worker/services/_declarative/genesis-productivity.yaml` | `canonical_name: genesis` + the `ports:` block (`ui` primary → 3000, `api` → 8765). |
| `genesis_worker/services/_declarative/genesis-agent-context.yaml` | `canonical_name: gac` + the `ports:` block (`ui` primary → 3001, `api` → 8766). |
| `genesis_worker/services/_declarative/{ntfy,bifrost,prometheus,freshrss,crawl4ai}.yaml` | one `canonical_name:` line each — no `ports:` block (they stay single-port). |
| `docs/notes/orchestrator-service-control.md` | the two new read-side fields on `GET /v1/services`. |
| `docs/tutorials/declarative-services.md` | the skeleton gains `canonical_name:` / `ports:`; a section states the rules. |
| `docs/arch/adr-049-declared-service-ports-and-canonical-names.md` | the ADR (written before the code). |
| `docs/arch/plans/plan-048-declared-service-ports-and-canonical-names.md` | this plan. |

**Not changed:** the API route layer, the detail schema, the compose templates, the twenty other
shipped specs, the worker's UI.

## 4. Tests

| File | Covers |
|---|---|
| `genesis_worker/tests/test_service_ports.py` (new) | the declaration matrix (one primary, duplicate labels, an unlabelled port, a non-DNS label, duplicate host ports, two ports on uv, `ports:` beside a legacy field, an invalid `canonical_name`, a marker host without a container port) and the derivation (the primary → listen / internal port, the secondaries → extra ports, the `$auto_port` use sites inside the block, the legacy form → one implicit port, the marker-backed endpoint). |
| `genesis_worker/tests/test_declarative_specs.py` | the shipped specs: the two compose services declare their ports and their canonical names; the five single-port services declare a canonical name and no `ports:` block. |
| `genesis_worker/tests/test_api.py` | `GET /v1/services` carries `canonical_name` and `ports`; the row stays backward compatible. |
| `genesis_worker/tests/test_facade.py` | `list_services()` carries both. |
| `genesis_worker/tests/test_port_allocator.py` | a service's second fixed port is reserved. |

## 5. QA gate

```bash
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

## 6. Landing

The worker lands **first**: the orchestrator consumes `canonical_name` and `ports`. The toolkit
change is backward compatible (an older worker → today's single route per service), so the two
PRs may merge in either order — the feature only works once the new worker is deployed.
