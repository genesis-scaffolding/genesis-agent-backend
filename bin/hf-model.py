#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "huggingface_hub>=0.30,<2",
# ]
# ///
"""Interactive Hugging Face GGUF model installer.

The script discovers the files in an HF model repository, lets the user pick
one main quantization and optional auxiliary files (mmproj/MTP/etc.), previews
the exact ``uvx hf download`` operation, and downloads the selected files into
the project's standard HF cache.

Usage:
    ./bin/hf-model.py ORG/MODEL
    ./bin/hf-model.py --root /path/to/models ORG/MODEL
    ./bin/hf-model.py --dry-run ORG/MODEL

The ``--dry-run`` option performs discovery, selection, and the HF dry-run,
but never asks to or proceeds to download files.  The normal workflow performs
the dry-run first and asks for confirmation before downloading.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError


GGUF_EXT = ".gguf"
SHARD_RE = re.compile(r"^(?P<base>.+)-(?P<number>\d{5})-of-(?P<count>\d{5})(?P<ext>\.gguf)$", re.IGNORECASE)


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int | None


@dataclass
class FileGroup:
    """One selectable file, or a group of shards for one selectable model."""

    paths: list[str]
    size: int | None
    role: str
    label: str

    @property
    def is_sharded(self) -> bool:
        return len(self.paths) > 1


# These names are deliberately conservative.  The existing catalog uses
# "mmproj" and a leading "mtp-" as its strong signals.  The extra markers
# prevent common adapter/projector files from appearing as main quant choices,
# while unusual GGUF names remain visible as "other" auxiliary files.
AUX_MARKERS = (
    "mmproj",
    "adapter",
    "lora",
    "controlnet",
    "text_encoder",
    "image_encoder",
    "vision_encoder",
    "vae",
    "draft",
)


def format_size(size: int | None) -> str:
    if size is None:
        return "size unknown"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return "size unknown"


def total_size(groups: Iterable[FileGroup]) -> int | None:
    sizes = [group.size for group in groups]
    if any(size is None for size in sizes):
        return None
    return sum(size for size in sizes if size is not None)


def classify_path(path: str) -> str:
    """Classify a remote GGUF path for the interactive selection UI."""
    name = Path(path).name.lower()
    if "mmproj" in name:
        return "mmproj"
    if name.startswith("mtp-"):
        return "mtp"
    if any(marker in name for marker in AUX_MARKERS):
        return "unsupported"
    return "main"


def fetch_files(repo_id: str, revision: str) -> list[RemoteFile]:
    """Fetch files from the Hub without downloading their contents."""
    api = HfApi()
    files: list[RemoteFile] = []
    try:
        tree = api.list_repo_tree(
            repo_id=repo_id,
            repo_type="model",
            revision=revision,
            recursive=True,
        )
        for item in tree:
            path = getattr(item, "path", None)
            if not path or not path.lower().endswith(GGUF_EXT):
                continue
            # RepoFolder entries have no size.  The GGUF filter normally makes
            # these impossible, but keep the check so a future API response
            # shape does not turn a folder into a downloadable file.
            if not hasattr(item, "size"):
                continue
            size = getattr(item, "size", None)
            files.append(RemoteFile(path=path, size=size))
    except HfHubHTTPError as exc:
        print(f"error: unable to inspect {repo_id}@{revision}: {exc}", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"error: unable to inspect {repo_id}@{revision}: {exc}", file=sys.stderr)
        return []

    return sorted(files, key=lambda item: item.path.lower())


def group_files(files: list[RemoteFile]) -> list[FileGroup]:
    """Group split GGUF files while preserving their role classification."""
    grouped: dict[str, list[RemoteFile]] = {}
    for item in files:
        match = SHARD_RE.match(item.path)
        key = match.group("base") + match.group("ext") if match else item.path
        grouped.setdefault(key, []).append(item)

    groups: list[FileGroup] = []
    for key, members in grouped.items():
        members.sort(key=lambda item: item.path.lower())
        role = classify_path(members[0].path)
        size = (
            sum(item.size for item in members if item.size is not None)
            if all(item.size is not None for item in members)
            else None
        )
        if len(members) == 1:
            label = members[0].path
        else:
            label = f"{key} ({len(members)} shards)"
        groups.append(
            FileGroup(
                paths=[item.path for item in members],
                size=size,
                role=role,
                label=label,
            )
        )

    role_order = {"main": 0, "mmproj": 1, "mtp": 2, "unsupported": 3}
    return sorted(groups, key=lambda group: (role_order[group.role], group.label.lower()))


def print_groups(title: str, groups: list[FileGroup]) -> None:
    print(f"\n{title}")
    for index, group in enumerate(groups, start=1):
        print(f"  [{index}] {group.label}  ({format_size(group.size)})")


def ask_single(groups: list[FileGroup], prompt: str) -> FileGroup | None:
    """Ask for one group using a portable numbered prompt."""
    if not groups:
        return None

    while True:
        try:
            answer = input(f"{prompt} [1-{len(groups)}, q]: ").strip().lower()
        except EOFError:
            print("\nCancelled.")
            return None
        if answer == "q":
            return None
        try:
            index = int(answer)
        except ValueError:
            print("Please enter a number or q to cancel.")
            continue
        if 1 <= index <= len(groups):
            return groups[index - 1]
        print(f"Please enter a number from 1 to {len(groups)}.")


def ask_auxiliary(groups: list[FileGroup]) -> list[FileGroup] | None:
    """Ask for optional auxiliaries; enforce one mmproj and one MTP maximum."""
    if not groups:
        print("\nNo auxiliary GGUF files found.")
        return []

    print_groups("Auxiliary files (optional; comma-separated, or 0 for none):", groups)
    default: list[int] = []
    mmprojs = [i for i, group in enumerate(groups) if group.role == "mmproj"]
    if len(mmprojs) == 1:
        default = [mmprojs[0] + 1]
    default_text = ",".join(str(index) for index in default) if default else "0"

    while True:
        try:
            answer = input(f"Select auxiliary files [{default_text}, q]: ").strip().lower()
        except EOFError:
            print("\nCancelled.")
            return None
        if not answer:
            answer = ",".join(str(index) for index in default) or "0"
        if answer == "q":
            return None
        if answer == "0":
            return []

        try:
            indexes = [int(part.strip()) for part in answer.split(",") if part.strip()]
        except ValueError:
            print("Please enter comma-separated numbers, 0, or q to cancel.")
            continue
        if not indexes or any(index < 1 or index > len(groups) for index in indexes):
            print(f"Please enter numbers from 1 to {len(groups)}, or 0.")
            continue
        if len(set(indexes)) != len(indexes):
            print("Each selection may only appear once.")
            continue

        selected = [groups[index - 1] for index in indexes]
        if sum(group.role == "mmproj" for group in selected) > 1:
            print("Please select at most one mmproj file.")
            continue
        if sum(group.role == "mtp" for group in selected) > 1:
            print("Please select at most one MTP/draft file.")
            continue
        return selected


def build_hf_command(
    repo_id: str,
    revision: str,
    cache_dir: Path,
    groups: list[FileGroup],
    *,
    dry_run: bool,
) -> list[str]:
    command = [
        "uvx",
        "hf",
        "download",
        "--cache-dir",
        str(cache_dir),
        "--revision",
        revision,
    ]
    if dry_run:
        command.append("--dry-run")
    command.append(repo_id)
    for group in groups:
        command.extend(group.paths)
    return command


def print_selected(main: FileGroup, auxiliaries: list[FileGroup]) -> None:
    selected = [main, *auxiliaries]
    print("\nSelected files:")
    print(f"  main: {main.label}")
    for group in auxiliaries:
        print(f"  {group.role}: {group.label}")
    print(f"  total: {format_size(total_size(selected))}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactively select and download GGUF files from Hugging Face."
    )
    parser.add_argument(
        "repo_id",
        help="Hugging Face model repository, for example unsloth/Qwen3.5-9B-MTP-GGUF",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Models root (default: MODELS_ROOT environment variable).",
    )
    parser.add_argument(
        "--revision",
        default="main",
        help="Hugging Face revision to inspect/download (default: main).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run discovery and the HF dry-run, then exit without downloading.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the final confirmation prompt after the dry-run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if shutil.which("uvx") is None:
        print("error: uvx is not on PATH; install uv first", file=sys.stderr)
        return 2

    root_value = args.root or os.environ.get("MODELS_ROOT")
    if not root_value:
        print("error: set MODELS_ROOT or pass --root PATH", file=sys.stderr)
        return 2

    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        print(f"error: models root is not a directory: {root}", file=sys.stderr)
        return 2
    cache_dir = root / "huggingface" / "hub"
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"Inspecting {args.repo_id}@{args.revision} ...")
    files = fetch_files(args.repo_id, args.revision)
    if not files:
        print("error: no GGUF files found, or the repository could not be inspected", file=sys.stderr)
        return 1

    groups = group_files(files)
    main_groups = [group for group in groups if group.role == "main"]
    auxiliary_groups = [group for group in groups if group.role in {"mmproj", "mtp"}]
    unsupported_groups = [group for group in groups if group.role == "unsupported"]
    if not main_groups:
        print("error: no main GGUF quantization candidates found", file=sys.stderr)
        return 1

    print(f"\nRepository: {args.repo_id}")
    print(f"Revision:   {args.revision}")
    print_groups("Main model variants (select one):", main_groups)
    main = ask_single(main_groups, "Select main model")
    if main is None:
        print("Cancelled.")
        return 0

    if unsupported_groups:
        print("\nOther GGUF files (not selected; unsupported by the current catalog):")
        for group in unsupported_groups:
            print(f"  - {group.label}  ({format_size(group.size)})")

    auxiliaries = ask_auxiliary(auxiliary_groups)
    if auxiliaries is None:
        print("Cancelled.")
        return 0

    selected = [main, *auxiliaries]
    print_selected(main, auxiliaries)
    print(f"\nCache: {cache_dir}")

    dry_command = build_hf_command(
        args.repo_id,
        args.revision,
        cache_dir,
        selected,
        dry_run=True,
    )
    print("\nRunning Hugging Face dry-run...\n")
    sys.stdout.flush()
    dry_result = subprocess.run(dry_command)
    if dry_result.returncode != 0:
        print("error: Hugging Face dry-run failed", file=sys.stderr)
        return dry_result.returncode

    if args.dry_run:
        print("\nDry-run only; no files were downloaded.")
        return 0

    if not args.yes:
        try:
            answer = input("\nProceed with this download? [y/N]: ").strip().lower()
        except EOFError:
            print("\nCancelled; no files were downloaded.")
            return 0
        if answer not in {"y", "yes"}:
            print("Cancelled; no files were downloaded.")
            return 0

    download_command = build_hf_command(
        args.repo_id,
        args.revision,
        cache_dir,
        selected,
        dry_run=False,
    )
    print("\nDownloading selected files...\n")
    sys.stdout.flush()
    result = subprocess.run(download_command)
    if result.returncode != 0:
        print("error: Hugging Face download failed", file=sys.stderr)
        return result.returncode

    print("\nDownload complete.")
    print(f"Next: make all ROOT={root}")
    print("Then: make up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
