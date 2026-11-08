# Plan: Service-served artefacts (ADR-043)

Phase A of the cross-repo task "generate pi model configuration for the whole
cluster". The orchestrator half lives in `genesis-infrastructure-toolkit`
(`docs/arch/plans/plan-015-cluster-pi-model-config.md`).

## Steps

1. **Contract** — `InferenceService.artefact(name, *, catalog, params)` returns
   `dict | None`, default `None` (ADR-043 §2). `export_for_agent` gains a
   keyword-only `provider=None` parameter.
2. **Exporter** — `export_pi_config.build_provider_from_configs` /
   `build_provider_for_catalog` take `provider` (replacing `hostname`).
   Explicit provider → verbatim key + prefixed model ids and names; no
   provider → today's standalone shape, derived key slugged.
3. **Plugin** — `LlamaSwapService.export_for_agent` threads `provider` through;
   `LlamaSwapService.artefact` serves `models.json` and reads the `provider`
   query parameter.
4. **Route** — `GET /v1/services/{name}/artefacts/{artefact}` in
   `genesis_worker/api/routes/services.py`: 404 unknown service, 404 unknown
   artefact, 500 on a plugin failure, query parameters passed through verbatim.
5. **Tests** — `genesis_worker/tests/test_agent_export.py` (explicit provider
   verbatim, prefixing, metadata copied verbatim, derived key still slugged)
   and `genesis_worker/tests/test_service_artefacts.py` (route shape,
   `?provider=p3`, the two 404s).
6. **Docs** — this ADR, `docs/arch/adr-043-service-served-artefacts.md`,
   and the cross-repo contract note
   `docs/notes/orchestrator-service-control.md`.

## Gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

## Out of scope

- The orchestrator side (Phase B): collection, assembly, the UI page, the CLI.
- Live-fleet end-to-end verification (Phase C): it needs the merged backend
  deployed to every node first.
