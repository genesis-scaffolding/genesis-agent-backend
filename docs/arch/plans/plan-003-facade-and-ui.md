# Plan 003: Facade, CLI, and Streamlit app

Implements [spec-003](../specs/spec-003-facade-and-ui.md) on top of [ADR-010](../adr-010-per-plugin-ui-pages.md).

## Working rules

- Branch: `feature/genesis-worker-ui` off `main`.
- `uv add` for any new dependency. `pyproject.toml` is updated by `uv`, not by hand.
- `Makefile`, `bin/`, and repo-root state files are not modified during v1.
- The running llama-swap on `:8080` is not stopped during validation. The UI is exercised against it.
- Streamlit binds to `0.0.0.0`; Tailscale + host firewall do the rest.
- Verification: `pytest -q`, `pyright`, `ruff check genesis_worker` all pass. Tests pass from any cwd.
- Commit at each chunk boundary. Single-line messages, no body.

## Chunks

### Chunk 1: Contract additions

1. `uv add streamlit` at repo root.
2. `genesis_worker/contracts/ui.py` — `UiPage` dataclass (label, icon, path). Re-export from `contracts/__init__.py`.
3. `genesis_worker/contracts/service.py` — add `ui_pages: list[UiPage]` abstract property to `InferenceService`.
4. `genesis_worker/contracts/source.py` — add `ui_pages: list[UiPage]` abstract property to `ModelSource`.
5. `genesis_worker/tests/_factories.py` — extend if any test mocks need updating.
6. Run `pytest`, `pyright`, `ruff`. Commit.

### Chunk 2: Metrics + facade additions

1. `genesis_worker/metrics/__init__.py` — empty.
2. `genesis_worker/metrics/system.py` — `MachineMetrics` dataclass + `collect_metrics()` using `psutil` and `pynvml`. Graceful degradation when no NVIDIA driver.
3. `genesis_worker/contracts/__init__.py` — re-export `UiPage`.
4. `genesis_worker/facade.py` — add `start_service`, `stop_service`, `service_status`, `collect_metrics` methods. Re-export `MachineMetrics` for convenience.
5. `genesis_worker/tests/test_metrics_system.py` — test `collect_metrics()` returns non-None CPU/RAM; GPU/VRAM may be None.
6. `genesis_worker/tests/test_facade.py` — extend with new method smoke tests.
7. Run all checks. Commit.

### Chunk 3: Plugin UI pages (llama_swap + huggingface)

1. `genesis_worker/services/llama_swap/ui/__init__.py` — empty.
2. `genesis_worker/services/llama_swap/ui/status.py` — landing page: state, Stop/Start, web UI link, config status, regenerate button, manage-config button.
3. `genesis_worker/services/llama_swap/ui/config_editor.py` — override editing only.
4. `genesis_worker/services/llama_swap/ui/recipes_view.py` — read-only structured recipes.
5. `genesis_worker/services/llama_swap/ui/pi_export.py` — preview/download/install.
6. `genesis_worker/sources/huggingface/ui/__init__.py` — empty.
7. `genesis_worker/sources/huggingface/ui/acquire.py` — landing page: form + wizard.
8. `genesis_worker/sources/huggingface/ui/session_list.py` — in-flight sessions list.
9. Implement `ui_pages` properties on `LlamaSwapService` and `HuggingFaceSource`.
10. Run all checks. Commit.

### Chunk 4: Framework UI pages

1. `genesis_worker/ui/__init__.py` — empty.
2. `genesis_worker/ui/dashboard.py` — system strip + services tiles + vault section.
3. `genesis_worker/ui/catalog.py` — read-only browse view.
4. `genesis_worker/ui/app.py` — ~50-line shell using `st.navigation`.
5. Run all checks. Commit.

### Chunk 5: CLI entry point + console script

1. `genesis_worker/cli/__init__.py` — empty.
2. `genesis_worker/cli/ui.py` — `main()` that shells out to `streamlit run` against `genesis_worker/ui/app.py`.
3. `genesis_worker/cli/up.py` — argparse + `worker.start_service` / `worker.stop_service`.
4. `genesis_worker/cli/catalog.py` — argparse + `worker.rescan_catalog`.
5. `genesis_worker/cli/config.py` — argparse + `worker.regenerate_service_config`.
6. `genesis_worker/cli/hf_model.py` — REPL driving `HfAcquireSession`.
7. `genesis_worker/cli/pi_models.py` — argparse + `worker.service("llama-swap").export_for_agent`.
8. `pyproject.toml` — `uv add` already updated for streamlit; add `[project.scripts]` entry: `genesis-worker-ui = "genesis_worker.cli.ui:main"`.
9. `genesis_worker/tests/test_cli_smoke.py` — `python -m genesis_worker.cli.<x> --help` exits 0.
10. `genesis_worker/tests/test_ui_pages.py` — verify `svc.ui_pages` for each registered plugin; paths exist inside `ui/`; first entry is the landing.
11. `genesis_worker/tests/test_app_shell.py` — page-discovery smoke test.
12. Run all checks. Commit.

### Chunk 6: Launch verification

1. `uv run genesis-worker-ui` starts; `curl -s http://127.0.0.1:8501/_stcore/health` returns 200.
2. Phone end-to-end deferred to user (manual, requires Tailscale).

## Post-v1 (Phase 10 retirement, referenced from ADR-008)

For each `bin/` script, in order:

1. `pi_models.py` → replace body with `from genesis_worker.cli.pi_models import main; raise SystemExit(main())`. Validate: `make pi-models.json` produces a content-equivalent `pi-models.json`. Delete the file. Update the Makefile target.
2. `catalog.py` → same. Validate: `make catalog`.
3. `build-config.py` → same. Validate: `make config`.
4. `hf_model.py` → same. Validate: `make install-model REPO=...`.
5. `up` → same. Validate: `make up`.
6. `bonsai-server` → move under `scripts/dev/` unchanged.

These steps are post-v1. Schedule a separate plan after the user has used the Streamlit UI on a phone for a while.