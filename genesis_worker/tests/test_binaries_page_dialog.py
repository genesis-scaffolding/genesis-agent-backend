"""The Binaries page: the installable form lives in a dialog, and it explains itself.

Two things are asserted here, both from the hands-on review of the page:

* the add/edit form is reachable through a dialog instead of filling the
  page, and the records stay visible behind it;
* every field of the form carries help text, so a user who has never
  seen the concept can fill it from the on-screen copy alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Selectbox, TextInput

from genesis_worker import GenesisWorker
from genesis_worker.contracts import InstallState
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.utils.installables.docker_image import DockerImageInstall
from genesis_worker.utils.installables.github_release import GithubReleaseInstall

PAGE = Path(__file__).resolve().parents[1] / "services" / "llama_swap" / "ui" / "binaries.py"


def _make_worker(tmp_path: Path) -> GenesisWorker:
    settings = Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
        )
    )
    return GenesisWorker(settings=settings)


@pytest.fixture(autouse=True)
def _offline_installables(monkeypatch: pytest.MonkeyPatch) -> None:
    """No registry or GitHub lookups from the page under test."""
    monkeypatch.setattr(GithubReleaseInstall, "available_versions", lambda self: [])
    monkeypatch.setattr(GithubReleaseInstall, "state", lambda self: InstallState.NOT_INSTALLED)
    monkeypatch.setattr(DockerImageInstall, "available_versions", lambda self: [])
    monkeypatch.setattr(DockerImageInstall, "state", lambda self: InstallState.NOT_INSTALLED)


def _open_page(tmp_path: Path) -> AppTest:
    at = AppTest.from_file(str(PAGE), default_timeout=30)
    at.session_state["worker"] = _make_worker(tmp_path)
    at.run()
    assert not at.error, [e.value for e in at.error]
    return at


def _button(at: AppTest, label: str, *, key_prefix: str | None = None):
    for button in at.button:
        if button.label != label:
            continue
        if key_prefix is not None and not (button.key or "").startswith(key_prefix):
            continue
        return button
    raise AssertionError(f"no {label!r} button (key prefix {key_prefix!r})")


def _dialog_fields(at: AppTest) -> dict[str, TextInput | Selectbox]:
    fields: dict[str, TextInput | Selectbox] = {}
    for widget in list(at.text_input) + list(at.selectbox):
        fields[widget.label] = widget
    return fields


def test_records_sit_under_the_installables_header(tmp_path: Path) -> None:
    at = _open_page(tmp_path)
    assert [s.value for s in at.subheader] == ["Installables"]
    assert "Add installable" in [b.label for b in at.button]
    assert _button(at, "Edit", key_prefix="installable-edit-") is not None


def test_add_button_opens_a_guided_form(tmp_path: Path) -> None:
    at = _open_page(tmp_path)
    _button(at, "Add installable").click().run()
    assert not at.error, [e.value for e in at.error]

    fields = _dialog_fields(at)
    assert set(fields) == {
        "GitHub repository URL",
        "Name",
        "Label (optional)",
        "Variant key (optional)",
        "Asset pattern (glob)",
        "Kind",
        "Backend",
    }
    for label, widget in fields.items():
        assert str(widget.help).strip(), f"the {label!r} field needs help text"

    assert _button(at, "Fetch release assets").help.strip()
    assert _button(at, "Add", key_prefix="FormSubmitter").help.strip()
    assert _button(at, "Cancel").help.strip()


def test_docker_kind_shows_a_worked_example(tmp_path: Path) -> None:
    at = _open_page(tmp_path)
    _button(at, "Add installable").click().run()
    at.selectbox[0].select("docker_image").run()
    assert not at.error, [e.value for e in at.error]

    fields = _dialog_fields(at)
    assert set(fields) == {
        "Name",
        "Label (optional)",
        "Image",
        "Tag pattern (glob, optional)",
        "Default tag",
        "Kind",
    }
    for label, widget in fields.items():
        assert str(widget.help).strip(), f"the {label!r} field needs help text"

    examples = [info.value for info in at.info]
    assert any("ghcr.io/ggml-org/llama.cpp" in text for text in examples), examples


def test_edit_button_prefills_the_dialog_and_renames_submit(tmp_path: Path) -> None:
    at = _open_page(tmp_path)
    _button(at, "Edit", key_prefix="installable-edit-llama-server-cuda").click().run()
    assert not at.error, [e.value for e in at.error]

    fields = _dialog_fields(at)
    assert fields["Name"].value == "llama-server-cuda"
    assert fields["GitHub repository URL"].value == (
        "https://github.com/genesis-scaffolding/llama.cpp-cuda"
    )
    assert fields["Variant key (optional)"].value == "cuda"
    assert _button(at, "Edit", key_prefix="FormSubmitter") is not None

    fields["Label (optional)"].set_value("my cuda build")
    at.run()
    _button(at, "Edit", key_prefix="FormSubmitter").click().run()
    assert not at.error, [e.value for e in at.error]

    worker = at.session_state["worker"]
    records = {record.name: record for record in worker.service("llama_swap").installable_records()}
    assert records["llama-server-cuda"].label == "my cuda build"
