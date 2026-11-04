"""Binaries — installables, records, and version-pick for llama-swap."""

from __future__ import annotations

from typing import Any

import streamlit as st

from genesis_worker.contracts import AcquireStateKind, AcquireView, InstallState
from genesis_worker.services.llama_swap.installables import (
    ensure_unique_installable_name,
    fork_slug,
    guess_backend,
    parse_repo_url,
)
from genesis_worker.utils.installables import (
    DockerImageRecord,
    GithubReleaseRecord,
    InstallableRecord,
    derive_asset_pattern,
)

SERVICE_NAME = "llama_swap"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title("Binaries")

st.caption(
    "One entry per installable. Active selection lives in ``installs/<name>/current``; "
    "pinning is via ``selections.yaml`` (read-only in v1, writeable in v1+1)."
)

_SESSION_KEY_PREFIX = "binaries/sessions"
_VERSIONS_KEY_PREFIX = "binaries/available_versions"
_DROP_PENDING_PREFIX = "binaries/drop_pending"
_DRAFT_KEY = "binaries/installable-draft"
_FORM_REVISION_KEY = "binaries/installable-form-revision"
_CANDIDATE_KEY = "binaries/release-candidate"
_FLASH_KEY = "binaries/installable-flash"
_LOOKUP_WARNING_KEY = "binaries/release-lookup-warning"
_DIALOG_KEY = "binaries/installable-dialog"

_KIND_GITHUB = "github_release"
_KIND_DOCKER = "docker_image"

_KIND_HELP = (
    "Where the binaries come from: `github_release` installs a binary from the latest "
    "release of a GitHub repository, `docker_image` pulls a container image from a registry."
)

_DOCKER_EXAMPLE = (
    "**Worked example** — a minimal docker-image installable: name `llama-server-image`, "
    "kind `docker_image`, image `ghcr.io/ggml-org/llama.cpp`, tag pattern `server-*`, "
    "default tag `server-cuda`, label `llama-server image (CUDA)`."
)


def _session_key(name: str) -> str:
    return f"{_SESSION_KEY_PREFIX}/{name}"


def _versions_key(name: str) -> str:
    return f"{_VERSIONS_KEY_PREFIX}/{name}"


def _drop_pending_key(name: str) -> str:
    return f"{_DROP_PENDING_PREFIX}/{name}"


def _form_revision() -> int:
    return int(st.session_state.get(_FORM_REVISION_KEY, 0))


def _set_default(key: str, value: Any) -> None:
    if key not in st.session_state:
        st.session_state[key] = value


def _record_payload(record: InstallableRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _persist_user_records(records: list[InstallableRecord]) -> None:
    previous_values = dict(worker.read_service_overrides(SERVICE_NAME))
    values = dict(previous_values)
    values["installables"] = [_record_payload(record) for record in records]
    try:
        worker.write_service_overrides(SERVICE_NAME, values)
        svc.set_installable_records(records)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        try:
            worker.write_service_overrides(SERVICE_NAME, previous_values)
        except (OSError, RuntimeError, TypeError, ValueError) as rollback_exc:
            raise RuntimeError(
                f"could not save installable records: {exc}; "
                f"the sidecar rollback also failed: {rollback_exc}"
            ) from rollback_exc
        raise


def _replace_user_record(
    records: list[InstallableRecord],
    replacement: InstallableRecord,
) -> list[InstallableRecord]:
    updated: list[InstallableRecord] = []
    replaced = False
    for record in records:
        if record.name == replacement.name:
            updated.append(replacement)
            replaced = True
        else:
            updated.append(record)
    if not replaced:
        updated.append(replacement)
    return updated


def _render_step(step: AcquireView) -> None:
    if step.kind == "complete":
        st.success(step.title or "complete")
    elif step.kind == "failed":
        st.error(f"{step.title or 'failed'} — {step.error or 'unknown error'}")
    elif step.kind == "cancelled":
        st.warning(step.title or "cancelled")
    elif step.kind == "fetching" and step.progress is not None:
        total = step.progress.bytes_total or step.total_bytes or 1
        pct = step.progress.bytes_done / total if total else 0
        st.progress(
            min(pct, 1.0), text=f"{step.title or 'fetching'} · {step.progress.bytes_done}/{total}"
        )
    else:
        st.info(step.title or step.kind)


def _close_dialog() -> None:
    st.session_state.pop(_DIALOG_KEY, None)
    st.session_state.pop(_DRAFT_KEY, None)
    st.session_state.pop(_CANDIDATE_KEY, None)
    st.session_state[_FORM_REVISION_KEY] = _form_revision() + 1


def _render_installable_form() -> None:
    revision = _form_revision()
    draft: dict[str, Any] = st.session_state.get(_DRAFT_KEY, {})
    editing = bool(draft)
    editing_name = str(draft.get("name", "")) if editing else ""

    st.write(
        "An installable tells the worker where to get a llama-swap binary or image. "
        "Fill in the fields below — the help text under each one says what to enter."
    )

    kind_options = [_KIND_GITHUB, _KIND_DOCKER]
    draft_kind = str(draft.get("kind", _KIND_GITHUB))
    kind_index = kind_options.index(draft_kind) if draft_kind in kind_options else 0
    kind = st.selectbox(
        "Kind",
        kind_options,
        index=kind_index,
        key=f"installable-kind-{revision}",
        disabled=editing,
        help=_KIND_HELP
        + (
            " The kind cannot be changed — remove the installable and add a new one instead."
            if editing
            else ""
        ),
    )

    selected_asset = None
    candidate = None
    github_url = ""
    if kind == _KIND_GITHUB:
        default_url = str(draft.get("repo_url", ""))
        if not default_url and draft.get("repo"):
            default_url = f"https://github.com/{draft['repo']}"
        url_key = f"installable-github-url-{revision}"
        _set_default(url_key, default_url)
        github_url = st.text_input(
            "GitHub repository URL",
            key=url_key,
            help="The repository that publishes the binaries, e.g. "
            "`https://github.com/ggml-org/llama.cpp` or `owner/repo`. "
            "The worker installs the matching asset of the latest release.",
        )

        if st.button(
            "Fetch release assets",
            key=f"installable-fetch-{revision}",
            help="Look up the latest release of the repository above and list its "
            "Linux x64 archives.",
        ):
            try:
                candidate = svc.list_release_candidates(github_url)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                st.session_state[_CANDIDATE_KEY] = None
                st.error(f"Could not fetch release metadata: {exc}")
            else:
                st.session_state[_CANDIDATE_KEY] = (github_url.strip(), candidate)
                if not editing:
                    owner, repo = candidate.repo.split("/", 1)
                    slug = fork_slug(owner, repo)
                    st.session_state[f"installable-name-{revision}"] = f"llama-server-{slug}"
                    st.session_state[f"installable-variant-{revision}"] = slug

        stored = st.session_state.get(_CANDIDATE_KEY)
        if stored is not None:
            fetched_url, fetched_candidate = stored
            if fetched_url == github_url.strip() and fetched_candidate.assets:
                candidate = fetched_candidate
                st.caption(f"Latest release: `{candidate.tag}`")
                labels = []
                for asset in candidate.assets:
                    size_mb = round((asset.size_bytes or 0) / 1_000_000, 1)
                    labels.append(f"{asset.name} ({size_mb} MB)")
                selected_label = st.selectbox(
                    "Release asset",
                    labels,
                    key=f"installable-asset-{revision}",
                    help="The archive of the latest release to install. Picking one fills "
                    "in the asset pattern and the backend below.",
                )
                selected_asset = next(
                    asset
                    for asset, label in zip(candidate.assets, labels, strict=True)
                    if label == selected_label
                )
    else:
        st.info(_DOCKER_EXAMPLE)

    variant = ""
    asset_pattern = ""
    backend = None
    image = ""
    tag_pattern = ""
    default_tag = "latest"

    with st.form(f"installable-form-{revision}"):
        name_key = f"installable-name-{revision}"
        _set_default(name_key, editing_name)
        name = st.text_input(
            "Name",
            key=name_key,
            disabled=editing,
            help="The install's own name, e.g. `llama-server-cuda`. It must be unique, "
            "it names the folder under `installs/`, and it cannot be renamed later.",
        )

        label_key = f"installable-label-{revision}"
        _set_default(label_key, str(draft.get("label", "") or ""))
        label = st.text_input(
            "Label (optional)",
            key=label_key,
            help="A friendlier name for the list, e.g. `llama-server (CUDA)`. "
            "Optional — the Name is shown when this is empty.",
        )

        if kind == _KIND_GITHUB:
            variant_key = f"installable-variant-{revision}"
            _set_default(variant_key, str(draft.get("variant", "") or ""))
            variant = st.text_input(
                "Variant key (optional)",
                key=variant_key,
                help="A short key for this build, e.g. `cuda`. It must be unique across "
                "installables and selects the binary from the command line.",
            )
            default_pattern = str(draft.get("asset_pattern", "") or "")
            if selected_asset is not None:
                candidate_tag = candidate.tag if candidate is not None else ""
                default_pattern = derive_asset_pattern(selected_asset.name, candidate_tag)
            pattern_key = (
                f"installable-pattern-{revision}-"
                f"{selected_asset.name if selected_asset else 'manual'}"
            )
            _set_default(pattern_key, default_pattern)
            asset_pattern = st.text_input(
                "Asset pattern (glob)",
                key=pattern_key,
                help="Glob matched against the release asset names, ignoring case, e.g. "
                "`llama-*-bin-ubuntu-x64.tar.gz`. Exactly one asset per release should match.",
            )
            backend_options = ["cuda", "vulkan", "rocm", "cpu"]
            default_backend = str(draft.get("backend", "") or "")
            if selected_asset is not None:
                default_backend = guess_backend(selected_asset.name)
            if default_backend not in backend_options:
                default_backend = "cpu"
            backend_key = f"installable-backend-{revision}"
            _set_default(backend_key, default_backend)
            backend = st.selectbox(
                "Backend",
                backend_options,
                key=backend_key,
                help="Which accelerator the binary needs — `cuda`, `vulkan`, `rocm` or "
                "`cpu`. It is read from the asset name when you fetch a release.",
            )
        else:
            image_key = f"installable-image-{revision}"
            _set_default(image_key, str(draft.get("image", "") or ""))
            image = st.text_input(
                "Image",
                key=image_key,
                help="The container image the worker pulls, without a tag, e.g. "
                "`ghcr.io/ggml-org/llama.cpp`.",
            )
            tag_key = f"installable-tag-pattern-{revision}"
            _set_default(tag_key, str(draft.get("tag_pattern", "") or ""))
            tag_pattern = st.text_input(
                "Tag pattern (glob, optional)",
                key=tag_key,
                help="Glob matched against the image tags in the registry, ignoring case, "
                "e.g. `server-*`. Leave it empty to offer every tag.",
            )
            default_tag_key = f"installable-default-tag-{revision}"
            _set_default(default_tag_key, str(draft.get("default_tag", "") or "latest"))
            default_tag = st.text_input(
                "Default tag",
                key=default_tag_key,
                help="The tag pulled when no version is picked, e.g. `server-cuda`. "
                "It should match the tag pattern.",
            )

        submitted = st.form_submit_button(
            "Edit" if editing else "Add",
            help="Save the installable and close this dialog.",
        )

    if st.button(
        "Cancel",
        key=f"installable-cancel-{revision}",
        help="Close this dialog without saving.",
    ):
        _close_dialog()
        st.rerun()

    if not submitted:
        return

    clean_name = name.strip()
    clean_variant = variant.strip() or None
    if not clean_name:
        st.error("Name is required.")
        return
    if clean_variant == "auto":
        st.error("Variant key 'auto' is reserved.")
        return
    try:
        ensure_unique_installable_name(
            svc.installable_records(),
            clean_name,
            editing_name=editing_name or None,
        )
    except ValueError as exc:
        st.error(str(exc))
        return
    existing_variants = {
        record.variant
        for record in svc.installable_records()
        if record.name != clean_name and record.variant
    }
    if clean_variant and clean_variant in existing_variants:
        st.error(f"Variant key {clean_variant!r} is already in use.")
        return

    common = {
        "name": clean_name,
        "label": label.strip() or None,
        "enabled": bool(draft.get("enabled", True)),
        "primary": bool(draft.get("primary", False)),
        "variant": clean_variant if kind == _KIND_GITHUB else None,
    }
    lookup_warning: str | None = None
    try:
        if kind == _KIND_GITHUB:
            owner, repo = parse_repo_url(github_url)
            try:
                svc.list_release_candidates(github_url)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                lookup_warning = str(exc)
            if not asset_pattern.strip():
                raise ValueError("Asset pattern is required")
            record: InstallableRecord = GithubReleaseRecord(
                **common,
                backend=backend,
                repo=f"{owner}/{repo}",
                asset_pattern=asset_pattern.strip(),
                binary_name=str(draft.get("binary_name", "llama-server")),
                checksums_asset_suffix=draft.get("checksums_asset_suffix"),
                source_url=draft.get("source_url"),
            )
        else:
            if not image.strip():
                raise ValueError("Image is required")
            record = DockerImageRecord(
                **common,
                image=image.strip(),
                tag_pattern=tag_pattern.strip() or None,
                default_tag=default_tag.strip() or "latest",
            )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        st.error(str(exc))
        return

    user_records = _replace_user_record(svc.user_installable_records(), record)
    try:
        _persist_user_records(user_records)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        st.error(f"Could not save installable records: {exc}")
        return
    st.session_state.pop(_DRAFT_KEY, None)
    st.session_state.pop(_DIALOG_KEY, None)
    st.session_state[_FORM_REVISION_KEY] = revision + 1
    st.session_state[_FLASH_KEY] = f"Saved installable {clean_name!r}."
    if lookup_warning:
        st.session_state[_LOOKUP_WARNING_KEY] = (
            "GitHub release metadata could not be verified; the record was saved anyway. "
            f"The installation path will validate the selected asset. ({lookup_warning})"
        )
    st.rerun()


def _render_installables_section() -> None:
    with st.container(border=True):
        st.subheader("Installables")
        st.write(
            "An installable is a source the worker can pull llama-swap binaries or "
            "container images from. Each one keeps its own versions and install state. "
            "Add, edit and remove them here. The bundled seeds cover the usual cases; "
            "your own records override a seed with the same name."
        )
        if svc.installable_records_error:
            st.error(
                "The saved installable records are invalid; only the bundled seeds are active. "
                f"{svc.installable_records_error}"
            )
        if st.button(
            "Add installable",
            key="installable-add",
            type="primary",
            help="Open the form for a new installable.",
        ):
            st.session_state[_DRAFT_KEY] = {}
            st.session_state[_DIALOG_KEY] = "add"
            st.session_state[_CANDIDATE_KEY] = None
            st.session_state[_FORM_REVISION_KEY] = _form_revision() + 1
        _render_record_list()


def _open_installable_dialog() -> None:
    mode = str(st.session_state.get(_DIALOG_KEY, "add"))
    title = "Add installable" if mode == "add" else "Edit installable"
    st.dialog(title, width="large", on_dismiss=_close_dialog)(_render_installable_form)()


def _render_record_list() -> None:
    seed_names = {record.name for record in svc.seed_installable_records()}
    user_names = {record.name for record in svc.user_installable_records()}
    installs = {installable.name: installable for installable in svc.installs()}

    for record in svc.installable_records():
        with st.container(border=True):
            cols = st.columns([3, 2, 2, 1])
            with cols[0]:
                label = record.label or record.name
                st.markdown(f"**{label}**")
                provenance = "seed" if record.name in seed_names else "user"
                if record.name in user_names:
                    provenance += " + seed override"
                st.caption(f"`{record.name}` · {record.kind} · {provenance}")
            with cols[1]:
                if record.kind == "github_release":
                    st.write(f"Source: `{record.repo}`")
                    st.write(f"Pattern: `{record.asset_pattern}`")
                else:
                    st.write(f"Image: `{record.image}`")
                    st.write(f"Tags: `{record.tag_pattern or '*'}`")
            with cols[2]:
                installable = installs.get(record.name)
                state = (
                    "disabled"
                    if not record.enabled
                    else (
                        "installed"
                        if (
                            installable is not None
                            and installable.state() == InstallState.INSTALLED
                        )
                        else "not installed"
                    )
                )
                st.write(f"Variant: `{record.variant or '—'}`")
                st.write(f"Backend: `{record.backend or '—'}`")
                st.write(f"State: **{state}**")

            action_cols = st.columns(3)
            with action_cols[0]:
                if st.button("Edit", key=f"installable-edit-{record.name}"):
                    st.session_state[_DRAFT_KEY] = _record_payload(record)
                    st.session_state[_DIALOG_KEY] = "edit"
                    st.session_state[_FORM_REVISION_KEY] = _form_revision() + 1
                    st.session_state[_CANDIDATE_KEY] = None
            with action_cols[1]:
                if record.enabled:
                    if st.button("Disable", key=f"installable-disable-{record.name}"):
                        try:
                            svc.disable_installable(record.name)
                            _persist_user_records(svc.user_installable_records())
                        except (OSError, RuntimeError, TypeError, ValueError) as exc:
                            st.error(str(exc))
                        else:
                            st.session_state[_FLASH_KEY] = f"Disabled {record.name!r}."
                            st.rerun()
                elif st.button("Enable", key=f"installable-enable-{record.name}"):
                    enabled_record = record.model_copy(update={"enabled": True})
                    updated = _replace_user_record(svc.user_installable_records(), enabled_record)
                    try:
                        _persist_user_records(updated)
                    except (OSError, RuntimeError, TypeError, ValueError) as exc:
                        st.error(str(exc))
                    else:
                        st.session_state[_FLASH_KEY] = f"Enabled {record.name!r}."
                        st.rerun()
            with action_cols[2]:
                if record.name in user_names and st.button(
                    "Remove override", key=f"installable-remove-{record.name}"
                ):
                    try:
                        if record.enabled:
                            svc.disable_installable(record.name)
                        remaining = [
                            item
                            for item in svc.user_installable_records()
                            if item.name != record.name
                        ]
                        _persist_user_records(remaining)
                    except (OSError, RuntimeError, TypeError, ValueError) as exc:
                        st.error(str(exc))
                    else:
                        st.session_state[_FLASH_KEY] = f"Removed override for {record.name!r}."
                        st.rerun()


_render_installables_section()

flash = st.session_state.pop(_FLASH_KEY, None)
if flash:
    st.success(flash)
lookup_warning = st.session_state.pop(_LOOKUP_WARNING_KEY, None)
if lookup_warning:
    st.warning(lookup_warning)

for installable in svc.installs():
    name = installable.name
    sess_key = _session_key(name)
    drop_key = _drop_pending_key(name)

    with st.expander(name, expanded=False):
        current = installable.installed_version()
        service_running = svc.is_running()
        in_flight = sess_key in st.session_state

        installed = installable.state() == InstallState.INSTALLED
        state_label = "installed" if installed else "not installed"
        st.write(f"State: **{state_label}** — resolved: **{current or '—'}**")
        if source_url := installable.source_url():
            st.caption(f"Source: [{source_url}]({source_url})")
        if service_running:
            st.caption(
                "Service is currently running — stop it from the Status page before uninstalling."
            )

        versions = installable.available_versions()
        st.session_state[_versions_key(name)] = versions

        if not versions:
            st.caption("No matching asset found upstream.")
        else:
            labels = [v.version for v in versions]
            try:
                idx = labels.index(current) if current else 0
            except ValueError:
                idx = 0
            choice = st.selectbox(
                "Version",
                labels,
                index=idx,
                key=f"version-{name}",
            )
            obj = next(v for v in versions if v.version == choice)
            size_mb = round((obj.size_bytes or 0) / 1_000_000, 1)
            st.caption(f"{size_mb} MB")

            cols = st.columns(3)
            with cols[0]:
                install_disabled = in_flight
                install_help = "An install is already in progress." if in_flight else None
                if st.button(
                    "Install" if not installed else "Reinstall",
                    key=f"install-{name}",
                    disabled=install_disabled,
                    help=install_help,
                ):
                    st.session_state[sess_key] = installable.install(version=choice)
                    st.rerun()
            with cols[1]:
                uninstall_disabled = service_running or current is None
                uninstall_help = (
                    "Stop the service first."
                    if service_running
                    else "Nothing installed."
                    if current is None
                    else None
                )
                if st.button(
                    "Uninstall",
                    key=f"uninstall-{name}",
                    disabled=uninstall_disabled,
                    help=uninstall_help,
                ):
                    try:
                        svc.uninstall_installable(name, version=current)
                    except RuntimeError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.pop(sess_key, None)
                        st.rerun()
            with cols[2]:
                if st.button("Refresh versions", key=f"refresh-{name}"):
                    installable.invalidate_versions_cache()
                    st.session_state.pop(_versions_key(name), None)
                    st.rerun()

        if in_flight:
            session = st.session_state[sess_key]
            current_step = session.view()

            if current_step.kind in (
                AcquireStateKind.COMPLETE,
                AcquireStateKind.FAILED,
                AcquireStateKind.CANCELLED,
            ):
                _render_step(current_step)
                if st.session_state.get(drop_key):
                    st.session_state.pop(sess_key, None)
                    st.session_state.pop(drop_key, None)
                    st.rerun()
                else:
                    st.session_state[drop_key] = True
                    if st.button("Dismiss", key=f"dismiss-{name}"):
                        st.session_state.pop(sess_key, None)
                        st.session_state.pop(drop_key, None)
                        st.rerun()
            else:

                @st.fragment(run_every="2s")
                def _progress(session=session, drop_key=drop_key) -> None:
                    step = session.view()
                    _render_step(step)
                    if step.kind in (
                        AcquireStateKind.COMPLETE,
                        AcquireStateKind.FAILED,
                        AcquireStateKind.CANCELLED,
                    ) and not st.session_state.get(drop_key):
                        st.session_state[drop_key] = True
                        st.rerun(scope="app")

                _progress()

                if st.button("Cancel", key=f"cancel-{name}"):
                    session.cancel()
                    st.rerun()


if st.session_state.get(_DIALOG_KEY):
    _open_installable_dialog()
