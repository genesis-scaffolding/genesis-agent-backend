# Plan 003: Facade, CLI, and Streamlit app

Implements [spec-003](../specs/spec-003-facade-and-ui.md).

## Working rules

- Branch: `feature/genesis-worker-ui` from `master`.
- Continue using `uv` for dependency changes.
- `Makefile` and `bin/` are not modified during this plan. CLI wrappers exist as `python -m genesis_worker.cli.*` and are wired into the Makefile only during Phase 10 retirement (post-v1).
- The running llama-swap on `:8080` is not stopped during Streamlit validation; the UI is exercised against it. A parallel test on `:8081` (if needed) uses the lifecycle module from plan-002.

## File-by-file

### Facade and CLI (chunk 1)

1. **Create branch** `feature/genesis-worker-ui`.
2. **`uv add streamlit`** at the repo root.
3. **Write `genesis_worker/metrics/system.py`** — `collect_metrics() -> MachineMetrics` using `psutil` for CPU/RAM and `pynvml` for GPU/VRAM. Handle the case where `pynvml` cannot find an NVIDIA driver (return `gpu_percent=None`, `vram_used_gb=None`, `vram_total_gb=None`).
4. **Write `genesis_worker/facade.py`** per spec-003.
5. **Write `genesis_worker/cli/__init__.py`** (empty).
6. **Write `genesis_worker/cli/catalog.py`** — argparse + `worker.rescan_catalog()`.
7. **Write `genesis_worker/cli/config.py`** — argparse + `worker.service("llama-swap").regenerate_config()`.
8. **Write `genesis_worker/cli/up.py`** — argparse + `worker.start_service(args.service)` / `worker.stop_service(args.service)`.
9. **Write `genesis_worker/cli/hf_model.py`** — argparse that drives the `HfAcquireSession` from terminal (REPL form): prints `step.title`, reads stdin choices, calls `worker.submit_acquire(sid, choice)`. Used by Phase 10 retirement; not wired into the Makefile in v1.
10. **Write `genesis_worker/cli/pi_models.py`** — argparse + `worker.service("llama-swap").export_for_agent()` + `write_models_json`.
11. **Write `genesis_worker/tests/test_facade.py`** per spec-003.
12. **Write `genesis_worker/tests/test_cli_smoke.py`** — `python -m genesis_worker.cli.<x> --help` exits 0 for each CLI.
13. **`uv run pytest`** — must pass.
14. **Commit chunk 1.**

### Streamlit app (chunk 2)

15. **Create directory structure:**
    ```bash
    mkdir -p streamlit_app/pages
    ```
16. **Write `streamlit_app/app.py`** per spec-003.
17. **Write `streamlit_app/pages/dashboard.py`** per spec-003.
18. **Write `streamlit_app/pages/catalog.py`** per spec-003.
19. **Write `streamlit_app/pages/acquire.py`** per spec-003.
20. **Write `streamlit_app/pages/config_editor.py`** per spec-003.
21. **Write `streamlit_app/pages/recipes_view.py`** per spec-003.
22. **Write `streamlit_app/pages/pi_export.py`** per spec-003.
23. **Write `streamlit_app/run.sh`** (chmod +x).
24. **Launch locally:**
    ```bash
    ./streamlit_app/run.sh &
    sleep 5
    curl -s http://127.0.0.1:8501/_stcore/health
    ```
    Expect `200 OK`.
25. **Manual smoke test (laptop browser is fine for chunk-2 commit):**
    - Open `http://127.0.0.1:8501/`.
    - Dashboard shows llama-swap as RUNNING (it's already up via `bin/up`).
    - Stop / Start buttons work.
    - Catalog page lists entries; Rescan works.
    - Acquire page starts a session (use a real or fake repo); wizard advances.
    - Config editor renders entries; toggle an override on a test entry; Regenerate writes to a temp file (the running config.yaml is untouched unless the user explicitly clicks Regenerate).
    - Recipes view shows `recipes.yaml` structured.
    - Pi export page previews / downloads / installs `pi-models.json`.
26. **Kill the local Streamlit process** (`pkill -f 'streamlit run'`).
27. **Commit chunk 2.**

### Phone end-to-end (chunk 3)

28. **Confirm Tailscale is up** on the worker host. Find the Tailscale IP:
    ```bash
    tailscale ip -4
    ```
29. **Allow `:8501` through the host firewall** (if applicable). Document the exact commands run (e.g. `sudo ufw allow from 100.64.0.0/10 to any port 8501`).
30. **Launch Streamlit bound to Tailscale interface:**
    ```bash
    ./streamlit_app/run.sh
    ```
    (The `--server.address 0.0.0.0` already binds all interfaces.)
31. **From the phone browser on Tailscale**, exercise the full scenario from spec-003 verification step 5:
    - Dashboard stop/start llama-swap.
    - Catalog rescan.
    - Acquire a real or fake HF repo through the wizard.
    - Toggle an override and Regenerate; observe `config.yaml` mtime change.
    - Recipes view.
    - Pi export preview + download + install.
32. **Verify the running llama-swap** is still healthy after the UI exercise (no crash from a stray click).
33. **`make all`** — must still pass; on-disk artifacts unchanged except where the user explicitly triggered writes via the UI.
34. **Wait for user approval**, then commit chunk 3.

## Notes

- The Streamlit app's `get_worker()` uses `@st.cache_resource` so the `GenesisWorker` instance is shared across reruns and across page navigations. The acquire-session dict is on the instance; sessions persist as long as the Streamlit server is running.
- `st.rerun()` is used to refresh the page after state-changing actions (start/stop a service, submit an acquire choice). Polling loops in `acquire.py` and `dashboard.py` use `time.sleep` + `st.rerun()` — acceptable for v1's coarse progress; v2 can use `st.fragment` or websocket-based push.
- The `config_editor.py` page must not modify `config.yaml` until the user clicks Regenerate. Toggling override checkboxes updates the in-memory overrides state; only the Regenerate button calls `service.regenerate_config()`. The "stale" badge appears when overrides have changed since the last regen.
- The phone end-to-end scenario is the gating verification for chunk 3. If Tailscale isn't reachable from the phone for any reason, document the issue and stop; do not skip the verification.
- Streamlit's default auth is none (the spec explicitly defers auth to v1.1+). The host firewall + Tailscale ACL are the only access control. Document this in the page header text or README.

## Post-v1 (Phase 10 retirement, referenced from ADR-008)

For each `bin/` script, in order:

1. `pi_models.py` → replace body with `from genesis_worker.cli.pi_models import main; raise SystemExit(main())`. Validate: `make pi-models.json` produces a content-equivalent `pi-models.json`. Delete the file. Update the Makefile target.
2. `catalog.py` → same. Validate: `make catalog`.
3. `build-config.py` → same. Validate: `make config`.
4. `hf_model.py` → same. Validate: `make install-model REPO=...` (real or fake repo).
5. `up` → same. Validate: `make up` brings up a tmux session; subsequent `make up` is idempotent; `tmux kill-session -t swap` returns 0.
6. `bonsai-server` → move under `scripts/dev/` unchanged.

These steps are post-v1. Schedule a separate plan after the user has used the Streamlit UI on a phone for a while.
