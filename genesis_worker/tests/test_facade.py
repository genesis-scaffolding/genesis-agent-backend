"""Tests for the GenesisWorker facade."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker import GenesisWorker
from genesis_worker.contracts import (
    AcquireState,
    AcquireStateKind,
    AcquireView,
    InstallInProgressError,
    InstallState,
    ServiceCapabilities,
    ServiceCapabilityError,
    ServiceInstall,
    ServiceState,
    ServiceStatus,
    StartResult,
    StopResult,
    UiPage,
)
from genesis_worker.utils.models import MachineMetrics


def test_facade_lists_services(tmp_path: Path) -> None:
    """Hermetic: isolated state_dir so the bootstrap doesn't auto-enable
    services on the real install."""
    from genesis_worker.settings import PathsSettings, Settings

    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = GenesisWorker(settings=settings)
    services = w.list_services()
    assert any(s.name == "llama_swap" for s in services)


def test_list_enabled_services_filters_by_registry_state(tmp_path: Path, monkeypatch) -> None:
    """list_enabled_services() must only return services the registry has enabled.

    Hermetic: uses a tmp state_dir so we don't pollute user state, and
    mocks ``is_available`` so the bootstrap doesn't auto-enable things
    behind our back.
    """
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.services.llama_swap import LlamaSwapService
    from genesis_worker.settings import PathsSettings, Settings
    from genesis_worker.utils.services.docker_service import DockerService

    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: False)
    # Patch both LlamaSwap and the Docker base class so the YAML-declared
    # sillytavern (which extends DockerService) reports unavailable during
    # the bootstrap walk. Patching happens before the registry is built;
    # patching the instance afterward is too late because the bootstrap
    # runs in ``__init__``.
    monkeypatch.setattr(DockerService, "is_available", lambda self: False)
    settings = Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
        )
    )
    w = _GW(settings=settings)
    monkeypatch.setattr(w.service("sillytavern"), "is_available", lambda: False)
    w.services.enable("llama_swap")

    enabled_names = {s.name for s in w.list_enabled_services()}
    all_names = {s.name for s in w.list_services()}
    assert "llama_swap" in enabled_names
    assert "sillytavern" not in enabled_names
    # list_services still returns everything; only list_enabled_services filters.
    assert all_names.issuperset(enabled_names)


def test_service_info_carries_category_and_description(tmp_path: Path) -> None:
    """ServiceInfo gains category + description fields (ADR-029).

    Hermetic test against tmp state_dir.
    """
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.contracts import ServiceCategory
    from genesis_worker.settings import PathsSettings, Settings

    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = _GW(settings=settings)

    llama = next(s for s in w.list_services() if s.name == "llama_swap")
    assert llama.category == ServiceCategory.LLM
    assert llama.description == "OpenAI-compatible LLM server"


def test_facade_returns_service_instance(tmp_path: Path) -> None:
    """Hermetic: isolated state_dir."""
    from genesis_worker.settings import PathsSettings, Settings

    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = GenesisWorker(settings=settings)
    svc = w.service("llama_swap")
    assert svc.capabilities().can_serve_llm


def test_facade_service_status(tmp_path: Path) -> None:
    """Hermetic: isolated state_dir."""
    from genesis_worker.settings import PathsSettings, Settings

    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = GenesisWorker(settings=settings)
    status = w.service_status("llama_swap")
    assert isinstance(status, ServiceStatus)
    assert status.state in (ServiceState.RUNNING, ServiceState.STOPPED, ServiceState.FAILED)


def test_facade_start_service_returns_start_result(tmp_path: Path, monkeypatch) -> None:
    """Facade wiring: ``start_service`` delegates to the service's ``start``.

    Hermetic: isolated state_dir so the bootstrap doesn't auto-enable
    llama-swap on the real install, and the service's ``start`` method
    is patched so we don't actually launch docker / talk to the running
    llama-server.
    """
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.services.llama_swap import LlamaSwapService
    from genesis_worker.settings import PathsSettings, Settings

    monkeypatch.setattr(
        LlamaSwapService,
        "start",
        lambda self: StartResult(ok=True, message="mock-start"),
    )
    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = _GW(settings=settings)
    result = w.start_service("llama_swap")
    assert isinstance(result, StartResult)
    assert result.ok is True


def test_facade_stop_service_returns_stop_result(tmp_path: Path, monkeypatch) -> None:
    """Facade wiring: ``stop_service`` delegates to the service's ``stop``.

    Hermetic: same isolation strategy as the start test. Without it,
    this test stops the real llama-server every time pytest runs —
    which is how a dev-loop llama-server gets repeatedly killed.
    """
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.services.llama_swap import LlamaSwapService
    from genesis_worker.settings import PathsSettings, Settings

    monkeypatch.setattr(
        LlamaSwapService,
        "stop",
        lambda self: StopResult(ok=True, message="mock-stop"),
    )
    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = _GW(settings=settings)
    result = w.stop_service("llama_swap")
    assert isinstance(result, StopResult)
    assert result.ok is True


def test_facade_collect_metrics(tmp_path: Path) -> None:
    """Hermetic: isolated state_dir."""
    from genesis_worker.settings import PathsSettings, Settings

    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = GenesisWorker(settings=settings)
    m = w.collect_metrics()
    assert isinstance(m, MachineMetrics)


def test_ui_pages_property_exists_with_concrete_default(tmp_path: Path) -> None:
    """Default ui_pages returns an empty list. Plugins override.

    Hermetic: isolated state_dir.
    """
    from genesis_worker.settings import PathsSettings, Settings

    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = GenesisWorker(settings=settings)
    svc = w.service("llama_swap")
    assert isinstance(svc.ui_pages, list)
    assert all(isinstance(p, UiPage) for p in svc.ui_pages)


def test_service_capabilities_distinguishes_web_ui(tmp_path: Path) -> None:
    """has_web_ui means the service's own web UI on its native port.

    Hermetic: isolated state_dir.
    """
    from genesis_worker.settings import PathsSettings, Settings

    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = GenesisWorker(settings=settings)
    caps = w.service("llama_swap").capabilities()
    assert isinstance(caps, ServiceCapabilities)
    # The contract documents that has_web_ui is about the service's own web UI
    # (e.g. llama-swap's :8080), not worker-managed Streamlit pages.
    assert caps.has_web_ui is True


def test_facade_catalog_persists_across_instances(tmp_path: Path, monkeypatch) -> None:
    """A catalog written by one worker survives across worker restarts."""
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.settings import PathsSettings, Settings

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings = Settings(paths=PathsSettings(state_dir=state_dir))
    w1 = _GW(settings=settings)
    first = w1.rescan_catalog()
    assert (state_dir / "catalog.json").is_file()

    w2 = _GW(settings=settings)
    loaded = w2.catalog()
    assert loaded.generated_at == first.generated_at
    assert loaded.content_hash == first.content_hash


def test_delete_model_removes_entry_and_directory(tmp_path: Path) -> None:
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.contracts import Catalog, ModelEntry, ModelPiece
    from genesis_worker.settings import PathsSettings, Settings
    from genesis_worker.utils.catalog_utils import compute_content_hash

    vault = tmp_path / "vault"
    model_dir = vault / "org" / "repo"
    model_dir.mkdir(parents=True)
    (model_dir / "model.gguf").write_text("weights")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings = Settings(
        paths=PathsSettings(state_dir=state_dir, vault_path=vault),
    )

    entry = ModelEntry(
        name="org/repo",
        source="huggingface",
        pieces=[
            ModelPiece(role="main", filename="model.gguf", path=model_dir / "model.gguf", bytes=7)
        ],
        total_bytes=7,
        directory=str(model_dir),
    )
    catalog = Catalog(
        root=str(vault),
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash=compute_content_hash([entry]),
        entries=[entry],
    )
    import json

    (state_dir / "catalog.json").write_text(catalog.model_dump_json())

    w = _GW(settings=settings)
    # prime the cache
    _ = w.catalog()

    w.delete_model("huggingface", "org/repo")

    assert not model_dir.exists()
    assert not any(e.name == "org/repo" for e in w.catalog().entries)
    loaded = json.loads((state_dir / "catalog.json").read_text())
    assert len(loaded["entries"]) == 0


def test_delete_model_removes_entry_when_directory_already_gone(tmp_path: Path) -> None:
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.contracts import Catalog, ModelEntry, ModelPiece
    from genesis_worker.settings import PathsSettings, Settings
    from genesis_worker.utils.catalog_utils import compute_content_hash

    vault = tmp_path / "vault"
    model_dir = vault / "org" / "repo"
    model_dir.mkdir(parents=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings = Settings(
        paths=PathsSettings(state_dir=state_dir, vault_path=vault),
    )

    entry = ModelEntry(
        name="org/repo",
        source="huggingface",
        pieces=[
            ModelPiece(role="main", filename="model.gguf", path=model_dir / "model.gguf", bytes=0)
        ],
        total_bytes=0,
        directory=str(model_dir),
    )
    catalog = Catalog(
        root=str(vault),
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash=compute_content_hash([entry]),
        entries=[entry],
    )
    (state_dir / "catalog.json").write_text(catalog.model_dump_json())

    w = _GW(settings=settings)
    _ = w.catalog()

    w.delete_model("huggingface", "org/repo")

    assert not any(e.name == "org/repo" for e in w.catalog().entries)


def test_delete_model_raises_for_unknown_entry(tmp_path: Path) -> None:
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.contracts import Catalog, ModelEntry, ModelPiece
    from genesis_worker.settings import PathsSettings, Settings
    from genesis_worker.utils.catalog_utils import compute_content_hash

    vault = tmp_path / "vault"
    model_dir = vault / "other" / "repo"
    model_dir.mkdir(parents=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings = Settings(
        paths=PathsSettings(state_dir=state_dir, vault_path=vault),
    )

    entry = ModelEntry(
        name="other/repo",
        source="huggingface",
        pieces=[
            ModelPiece(role="main", filename="model.gguf", path=model_dir / "model.gguf", bytes=0)
        ],
        total_bytes=0,
        directory=str(model_dir),
    )
    catalog = Catalog(
        root=str(vault),
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash=compute_content_hash([entry]),
        entries=[entry],
    )
    (state_dir / "catalog.json").write_text(catalog.model_dump_json())

    w = _GW(settings=settings)
    _ = w.catalog()

    import pytest

    with pytest.raises(ValueError, match="No entry found"):
        w.delete_model("huggingface", "nonexistent/repo")


# --- User overrides + refresh_config (ADR-034) ---------------------------


def _hermetic_settings(tmp_path: Path):
    """Hermetic Settings isolated from the real install + state dir."""
    from genesis_worker.settings import PathsSettings, Settings

    return Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
        )
    )


def test_user_overrides_path_resolves_under_config_dir(tmp_path: Path) -> None:
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    assert w.user_overrides_path() == tmp_path / "config" / "user-overrides.env"


def test_read_user_overrides_returns_empty_when_absent(tmp_path: Path) -> None:
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    assert w.read_user_overrides() == {}


def test_write_user_overrides_round_trips(tmp_path: Path) -> None:
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    values = {"GENESIS_PATHS__VAULT_PATH": "/srv/vault"}
    w.write_user_overrides(values)
    assert w.read_user_overrides() == values


def test_write_user_overrides_refuses_secrets(tmp_path: Path) -> None:
    """Secrets belong to the SecretsAccessor path (ADR-012), not the override file."""
    import pytest

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    with pytest.raises(ValueError, match="cannot set secrets"):
        w.write_user_overrides({"GENESIS_SECRETS__GITHUB_TOKEN": "ghp_steal_me"})


def test_refresh_config_preserves_facade_identity(tmp_path: Path) -> None:
    """refresh_config mutates the facade in place — no new GenesisWorker."""
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    sources_before = w._source_registry  # noqa: SLF001
    services_before = w._service_registry  # noqa: SLF001
    w.refresh_config()
    # Same facade object; its internal registries are the *new* ones
    # (rebuilt), not the originals.
    assert w._source_registry is not sources_before  # noqa: SLF001
    assert w._service_registry is not services_before  # noqa: SLF001
    # And list_sources() still works post-refresh.
    assert w.list_sources()  # non-empty list


def test_refresh_config_picks_up_overrides(tmp_path: Path) -> None:
    """A vault_path written to the override file is honoured after refresh."""
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    new_vault = tmp_path / "alt-vault"
    w.write_user_overrides({"GENESIS_PATHS__VAULT_PATH": str(new_vault)})
    w.refresh_config()
    assert w.settings.paths.vault_path == new_vault
    assert w.settings.paths.resolved_vault_path == new_vault


def test_refresh_config_drops_catalog_cache(tmp_path: Path) -> None:
    """After refresh, the next catalog() call walks the new vault."""
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    _ = w.catalog()  # prime cache
    assert w._catalog_cache is not None  # noqa: SLF001 — diagnostic
    w.refresh_config()
    assert w._catalog_cache is None  # noqa: SLF001


def test_refresh_config_leaves_state_intact_on_failure(tmp_path: Path) -> None:
    """A malformed override raises but the facade survives unchanged.

    The 'construct-first, swap-last' safety guarantee — a bad save is
    loud and non-destructive (ADR-034).
    """
    import pytest

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    vault_before = w.settings.paths.resolved_vault_path
    sources_before = list(w.list_sources())
    services_before = list(w.list_services())

    w.user_overrides_path().parent.mkdir(parents=True, exist_ok=True)
    w.user_overrides_path().write_text("NOKEY\n")

    with pytest.raises(ValueError, match="malformed override"):
        w.refresh_config()

    assert w.settings.paths.resolved_vault_path == vault_before
    assert [s.name for s in w.list_sources()] == [s.name for s in sources_before]
    assert [s.name for s in w.list_services()] == [s.name for s in services_before]


def test_snapshot_settings_includes_path_knobs(tmp_path: Path) -> None:
    """The Settings page renders framework knobs only (ADR-034)."""
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    snapshots = w.snapshot_settings()
    names = {s.name for s in snapshots}
    # Framework knobs:
    assert "paths.vault_path" in names
    assert "paths.media_vault_path" in names
    assert "paths.data_dir" in names
    assert "paths.config_dir" in names
    assert "paths.cache_dir" in names
    assert "paths.state_dir" in names
    assert "paths.log_dir" in names
    # Per-source local_path knobs:
    assert "sources.huggingface.local_path" in names
    assert "sources.lmstudio.local_path" in names
    # Service-specific knobs are NOT here:
    assert not any(s.name.startswith("services.") for s in snapshots)


def test_snapshot_settings_marks_user_overrides_source(tmp_path: Path) -> None:
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    w.write_user_overrides({"GENESIS_PATHS__VAULT_PATH": "/srv/vault"})
    snapshots = {s.name: s for s in w.snapshot_settings()}
    assert snapshots["paths.vault_path"].source == "user_overrides"


def test_snapshot_settings_includes_media_vault_path(tmp_path: Path) -> None:
    """ADR-037 — ``paths.media_vault_path`` shows up next to ``paths.vault_path``."""
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    snapshots = {s.name: s for s in w.snapshot_settings()}
    assert "paths.media_vault_path" in snapshots
    entry = snapshots["paths.media_vault_path"]
    assert entry.override_key == "GENESIS_PATHS__MEDIA_VAULT_PATH"


def test_snapshot_settings_picks_up_media_vault_override(tmp_path: Path) -> None:
    """A user-overrides file value for the media vault labels the snapshot source."""
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    w.write_user_overrides({"GENESIS_PATHS__MEDIA_VAULT_PATH": "/srv/media"})
    snapshots = {s.name: s for s in w.snapshot_settings()}
    # Mirror ``paths.vault_path`` behaviour: the override file labels the
    # source; the resolved value updates only after ``refresh_config``.
    assert snapshots["paths.media_vault_path"].source == "user_overrides"


def test_refresh_config_applies_media_vault_override(tmp_path: Path) -> None:
    """Writing the override key + ``refresh_config`` updates the resolved media vault path."""
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    new_media = tmp_path / "media"
    w.write_user_overrides({"GENESIS_PATHS__MEDIA_VAULT_PATH": str(new_media)})
    w.refresh_config()
    assert w.settings.paths.media_vault_path == new_media
    assert w.settings.paths.resolved_media_vault_path == new_media


# --- install / start / restart (ADR-038) -----------------------------------


def _stub_installable_with_acquire(state: InstallState, final_kind: AcquireStateKind):
    """Build a minimal ``ServiceInstall`` stub for facade install tests.

    Returns a ``(installable, session_factory)`` pair. The session
    factory builds a fresh AcquireSession-shaped object on each
    ``install()`` call so each test owns its own session state.
    """
    from genesis_worker.contracts import AcquireSession

    class _StubInstallable(ServiceInstall):
        name = "stub-installable"

        def state(self) -> InstallState:
            return state

        def installed_version(self) -> str | None:
            # The facade reads installed_version() *after* a successful install.
            # The stub doesn't mutate state on install — it just returns the
            # session's terminal view — so always report v1 once the facade
            # has called install() at least once.
            return "v1"

        def available_versions(self):  # type: ignore[override]
            return []

        def binary_path(self):  # type: ignore[override]
            return None

        def install(self, *, version: str | None = None):
            class _StubSession(AcquireSession):
                source_name = "stub"

                @property
                def repo_id(self) -> str:
                    return "stub/repo"

                @property
                def state(self) -> AcquireState:
                    return AcquireState(kind=final_kind, repo_id="stub/repo")

                def view(self) -> AcquireView:
                    return AcquireView(kind=final_kind, title="done", can_cancel=False)

                def submit(self, choice) -> None:
                    return None

                def cancel(self) -> None:
                    return None

                def wait(self) -> AcquireView:
                    return AcquireView(kind=final_kind, title="done", can_cancel=False)

            return _StubSession()

        def uninstall(self, *, version: str | None = None) -> None:
            return None

    return _StubInstallable()


def test_install_service_idempotent_when_already_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling install on a service whose primary installable is already present
    is a no-op that returns the existing version."""

    from genesis_worker.services.llama_swap import LlamaSwapService

    stub = _stub_installable_with_acquire(InstallState.INSTALLED, AcquireStateKind.COMPLETE)
    monkeypatch.setattr(LlamaSwapService, "installs", lambda self: [stub])
    monkeypatch.setattr(LlamaSwapService, "primary_installable", lambda self: stub)

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    result = w.install_service("llama_swap")
    assert result == {"installed": True, "version": "v1"}


def test_install_service_runs_primary_installable_to_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A not-yet-installed service runs the installable; the facade returns the resolved version."""

    from genesis_worker.services.llama_swap import LlamaSwapService

    stub = _stub_installable_with_acquire(InstallState.NOT_INSTALLED, AcquireStateKind.COMPLETE)
    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: False)
    monkeypatch.setattr(LlamaSwapService, "installs", lambda self: [stub])
    monkeypatch.setattr(LlamaSwapService, "primary_installable", lambda self: stub)

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    result = w.install_service("llama_swap")
    assert result == {"installed": True, "version": "v1"}


def test_install_service_raises_capability_error_when_can_install_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytest

    from genesis_worker.services.llama_swap import LlamaSwapService

    # Default can_install is True on llama-swap, so override capabilities.
    monkeypatch.setattr(
        LlamaSwapService,
        "capabilities",
        lambda self: ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=True,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=False,
            can_install=False,
        ),
    )

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    with pytest.raises(ServiceCapabilityError, match="cannot be installed"):
        w.install_service("llama_swap")


def test_install_service_raises_capability_error_when_installs_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytest

    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(LlamaSwapService, "installs", lambda self: [])

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    with pytest.raises(ServiceCapabilityError, match="cannot be installed"):
        w.install_service("llama_swap")


def test_install_service_raises_in_progress_when_lock_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second concurrent caller gets InstallInProgressError immediately."""
    import pytest

    from genesis_worker.services.llama_swap import LlamaSwapService

    stub = _stub_installable_with_acquire(InstallState.NOT_INSTALLED, AcquireStateKind.COMPLETE)
    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: False)
    monkeypatch.setattr(LlamaSwapService, "installs", lambda self: [stub])
    monkeypatch.setattr(LlamaSwapService, "primary_installable", lambda self: stub)

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    # Externally hold the lock — simulates a second caller arriving
    # before the first finishes.
    w._install_lock_for("llama_swap").acquire()
    try:
        with pytest.raises(InstallInProgressError, match="already in progress"):
            w.install_service("llama_swap")
    finally:
        w._install_lock_for("llama_swap").release()


def test_install_service_raises_runtime_when_acquire_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route layer translates ``RuntimeError`` from a failed acquire to ``500``."""
    import pytest

    from genesis_worker.services.llama_swap import LlamaSwapService

    stub = _stub_installable_with_acquire(InstallState.NOT_INSTALLED, AcquireStateKind.FAILED)
    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: False)
    monkeypatch.setattr(LlamaSwapService, "installs", lambda self: [stub])
    monkeypatch.setattr(LlamaSwapService, "primary_installable", lambda self: stub)

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    with pytest.raises(RuntimeError, match="install failed"):
        w.install_service("llama_swap")


def test_install_service_raises_runtime_when_acquire_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytest

    from genesis_worker.services.llama_swap import LlamaSwapService

    stub = _stub_installable_with_acquire(InstallState.NOT_INSTALLED, AcquireStateKind.CANCELLED)
    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: False)
    monkeypatch.setattr(LlamaSwapService, "installs", lambda self: [stub])
    monkeypatch.setattr(LlamaSwapService, "primary_installable", lambda self: stub)

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    with pytest.raises(RuntimeError, match="install cancelled"):
        w.install_service("llama_swap")


def test_start_service_with_config_sets_pending_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator's config body flows into the service's transient attribute.

    The materialize_orchestrator_config hook reads this attribute
    during start; here we just verify the facade sets it correctly.
    """

    from genesis_worker.services.llama_swap import LlamaSwapService

    captured: dict = {}

    def _capture(self) -> StartResult:
        captured["pending"] = self._pending_orchestrator_config
        return StartResult(ok=True, message="mock-start")

    monkeypatch.setattr(LlamaSwapService, "start", _capture)
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    cfg = {"providers": {"openai": {"keys": [{"name": "k"}]}}}
    result = w.start_service("llama_swap", config=cfg)
    assert result.ok is True
    assert captured["pending"] == cfg


def test_start_service_clears_pending_attribute_after_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transient attribute is cleared in a ``finally`` block — no leakage."""

    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(
        LlamaSwapService, "start", lambda self: StartResult(ok=True, message="mock-start")
    )
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    w.start_service("llama_swap", config={"x": 1})
    svc = w.service("llama_swap")
    assert svc._pending_orchestrator_config is None


def test_start_service_clears_pending_attribute_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed start still clears the attribute — no leakage on the error path."""
    import pytest

    from genesis_worker.services.llama_swap import LlamaSwapService

    def _raise(self) -> StartResult:
        raise RuntimeError("start exploded")

    monkeypatch.setattr(LlamaSwapService, "start", _raise)
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    with pytest.raises(RuntimeError):
        w.start_service("llama_swap", config={"x": 1})
    svc = w.service("llama_swap")
    assert svc._pending_orchestrator_config is None


def test_start_service_without_config_leaves_pending_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing call sites that omit ``config`` see ``_pending_orchestrator_config=None``.

    Source-compatible: every existing caller (Streamlit UI, CLI,
    configure panel) calls ``worker.start_service(name)`` and expects
    no orchestrator config to be materialised.
    """

    from genesis_worker.services.llama_swap import LlamaSwapService

    captured: dict = {}

    def _capture(self) -> StartResult:
        captured["pending"] = self._pending_orchestrator_config
        return StartResult(ok=True, message="mock-start")

    monkeypatch.setattr(LlamaSwapService, "start", _capture)
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    w.start_service("llama_swap")
    assert captured["pending"] is None


def test_start_service_runs_install_first_when_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unavailable service delegates to ``install_service`` before ``start``.

    The orchestrator's contract: "Not installed → install first
    (delegates to the install path; may take minutes)." We don't run a
    real install — we verify the delegation order.
    """

    from genesis_worker.services.llama_swap import LlamaSwapService

    call_order: list[str] = []

    def _install(self, name: str) -> dict:
        call_order.append("install")
        return {"installed": True, "version": "v1"}

    def _start(self) -> StartResult:
        call_order.append("start")
        return StartResult(ok=True, message="mock-start")

    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: False)
    monkeypatch.setattr(LlamaSwapService, "start", _start)

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    # Monkeypatch the facade method to record the call.
    monkeypatch.setattr(w, "install_service", lambda name: _install(w, name))
    w.start_service("llama_swap")
    assert call_order == ["install", "start"]


def test_start_service_stops_before_starting_when_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running service is stopped first; start follows with the new config."""

    from genesis_worker.services.llama_swap import LlamaSwapService

    call_order: list[str] = []

    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: True)
    monkeypatch.setattr(LlamaSwapService, "is_running", lambda self: True)
    monkeypatch.setattr(
        LlamaSwapService,
        "stop",
        lambda self: (call_order.append("stop"), StopResult(ok=True, message="mock"))[1],
    )
    monkeypatch.setattr(
        LlamaSwapService,
        "start",
        lambda self: (call_order.append("start"), StartResult(ok=True, message="mock"))[1],
    )

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    w.start_service("llama_swap", config={"x": 1})
    assert call_order == ["stop", "start"]


def test_start_service_returns_failed_result_when_stop_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the pre-stop fails, the facade returns a failed StartResult — it does not retry blindly."""

    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: True)
    monkeypatch.setattr(LlamaSwapService, "is_running", lambda self: True)
    monkeypatch.setattr(
        LlamaSwapService, "stop", lambda self: StopResult(ok=False, message="container stuck")
    )

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    result = w.start_service("llama_swap", config={"x": 1})
    assert result.ok is False
    assert "failed to stop before start" in result.message


def test_restart_service_delegates_to_start_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``restart_service`` is a thin wrapper over ``start_service(config=...)``."""

    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(
        LlamaSwapService, "start", lambda self: StartResult(ok=True, message="mock-start")
    )
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    seen: dict = {}
    real_start = w.start_service

    def _capture(name, *, config=None):
        seen["name"] = name
        seen["config"] = config
        return real_start(name, config=config)

    monkeypatch.setattr(w, "start_service", _capture)
    cfg = {"providers": {}}
    w.restart_service("llama_swap", config=cfg)
    assert seen == {"name": "llama_swap", "config": cfg}


def test_install_service_unknown_service_raises_keyerror(
    tmp_path: Path,
) -> None:
    """An unknown service name surfaces as ``KeyError`` (route → 404)."""
    import pytest

    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    with pytest.raises(KeyError):
        w.install_service("does-not-exist")


def test_install_lock_lazy_creates_per_service(tmp_path: Path) -> None:
    """Two distinct service names get two distinct locks, both created lazily."""
    settings = _hermetic_settings(tmp_path)
    w = GenesisWorker(settings=settings)
    lock_a = w._install_lock_for("a")
    lock_b = w._install_lock_for("b")
    assert lock_a is not lock_b
    # Same name returns the same lock.
    assert w._install_lock_for("a") is lock_a
