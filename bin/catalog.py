#!/usr/bin/env python3
"""Generate a YAML catalog of HuggingFace and LM Studio models on disk.

Walks a root directory looking for:
  - <root>/huggingface/hub/   (HF cache layout)
  - <root>/lmstudio/models/   (LM Studio layout)

For HuggingFace, the live snapshot is read from ``refs/main`` and only that
snapshot is enumerated. For LM Studio, each ``<publisher>/<model-dir>`` is
treated as one model.

Usage:
    ./catalog.py                  # catalogs cwd
    ./catalog.py /path/to/root    # catalogs a different root
    ./catalog.py -o out.yaml      # write to a different file

A human-readable ``MODEL_CATALOG.md`` is written next to the YAML output by
default, with one section per model entry. Disable with ``--no-markdown``.

The script is read-only: it never modifies the model files.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Recognized component subdirectory names inside an HF snapshot.
# A weight file under one of these is given the component name as its role
# (e.g. transformer/diffusion_pytorch_model.safetensors -> role: transformer).
COMPONENT_DIRS = {
    "text_encoder",
    "transformer",
    "unet",
    "vae",
    "prior",
    "image_encoder",
    "controlnet",
    "denoiser",
    "decoder",
    "encoder",
    "scheduler",
}

# File extensions treated as model weights.
WEIGHT_EXTS = {
    ".gguf",
    ".safetensors",
    ".bin",
    ".pt",
    ".pth",
    ".ckpt",
}

# Filenames we ignore when scanning snapshots / model dirs.
SKIP_FILENAMES = {
    ".gitattributes",
    "README.md",
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "NOTICE.txt",
    "USE_POLICY.md",
    ".DS_Store",
}


def _write_if_changed(path: Path, payload: str) -> bool:
    """Write ``payload`` to ``path`` only if the existing contents differ.

    Preserving the existing mtime when the content is unchanged is what
    keeps llama-swap's -watch-config (and any other consumer that watches
    these generated files) from reloading on a no-op rebuild. Returns
    True when a write actually happened.
    """
    try:
        existing = path.read_text()
    except FileNotFoundError:
        path.write_text(payload)
        return True
    if existing == payload:
        return False
    path.write_text(payload)
    return True


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(path: Path) -> str:
    """Return the role tag for ``path`` (main/mmproj/mtp/<component>/config)."""
    name_lower = path.name.lower()
    ext = path.suffix.lower()

    # Multimodal projector / MTP draft are detected by filename first so they
    # override the component-dir heuristic below (in case someone puts an
    # mmproj inside a text_encoder/ dir, etc.).
    if "mmproj" in name_lower:
        return "mmproj"
    # MTP draft models are always stand-alone files with names like
    # ``mtp-<base-name>.gguf``. The token "mtp" appearing inside a model
    # name (e.g. ``...-MTP-Preserved-Q4_K.gguf``) just means MTP is baked
    # into the main weights -- not a separate draft file.
    if name_lower.startswith("mtp-"):
        return "mtp"

    parent_name = path.parent.name.lower()
    if ext in WEIGHT_EXTS:
        if parent_name in COMPONENT_DIRS:
            return parent_name
        return "main"

    # Tokenizer / config / metadata files.
    return "config"


# ---------------------------------------------------------------------------
# HuggingFace walker
# ---------------------------------------------------------------------------


def walk_huggingface(hub_dir: Path) -> list[dict]:
    """Enumerate HF repos. Return one entry per ``models--*`` directory."""
    if not hub_dir.is_dir():
        return []

    entries: list[dict] = []
    for repo_dir in sorted(hub_dir.iterdir()):
        if not repo_dir.is_dir() or not repo_dir.name.startswith("models--"):
            continue

        # models--org--repo -> org/repo. Repo name itself may contain "--"
        # in theory, but in practice the org is single-segment.
        parts = repo_dir.name.split("--")
        if len(parts) < 3:
            continue
        repo_id = f"{parts[1]}/{'--'.join(parts[2:])}"

        refs_main = repo_dir / "refs" / "main"
        snapshots_dir = repo_dir / "snapshots"
        if not refs_main.is_file() or not snapshots_dir.is_dir():
            continue

        try:
            sha = refs_main.read_text().strip()
        except OSError:
            continue

        snapshot_dir = snapshots_dir / sha
        if not snapshot_dir.is_dir():
            continue

        pieces, total_bytes = _collect_pieces(snapshot_dir)

        notes = _summarize_notes(pieces, partial=())

        entries.append(
            {
                "name": repo_id,
                "snapshot": sha,
                "directory": str(snapshot_dir.resolve()),
                "total_bytes": total_bytes,
                "pieces": pieces,
                "notes": notes,
            }
        )

    return entries


def _collect_pieces(snapshot_dir: Path) -> tuple[list[dict], int]:
    """Walk a snapshot dir (or any dir of weight/config files) and return
    a (pieces, total_bytes) pair, sorted by role then filename."""
    pieces: list[dict] = []
    total_bytes = 0

    for p in sorted(snapshot_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name in SKIP_FILENAMES:
            continue
        # Resolve symlinks to get the real blob path and real size.
        real = p.resolve()
        try:
            size = real.stat().st_size
        except OSError:
            continue
        role = classify(p)
        pieces.append(
            {
                "role": role,
                "filename": str(p.relative_to(snapshot_dir)),
                "path": str(real),
                "bytes": size,
            }
        )
        total_bytes += size

    pieces.sort(key=lambda piece: (_role_sort_key(piece["role"]), piece["filename"]))
    return pieces, total_bytes


def _role_sort_key(role: str) -> tuple[int, str]:
    # main weights first, then component weights, then aux (mmproj, mtp),
    # then configs.
    order = {"main": 0, "transformer": 1, "unet": 1, "denoiser": 1,
             "text_encoder": 2, "image_encoder": 2, "encoder": 2,
             "decoder": 2, "vae": 3, "prior": 3, "controlnet": 3,
             "scheduler": 3,
             "mmproj": 4, "mtp": 5, "config": 6}
    return (order.get(role, 9), role)


def _summarize_notes(pieces: list[dict], partial: tuple[str, ...]) -> list[str]:
    notes: list[str] = []
    if not any(p["role"] != "config" for p in pieces):
        notes.append("no model weights on disk")
    if partial:
        names = ", ".join(partial)
        notes.append(f"partial download in progress (skipped): {names}")
    return notes


# ---------------------------------------------------------------------------
# LM Studio walker
# ---------------------------------------------------------------------------


def walk_lmstudio(models_dir: Path) -> list[dict]:
    """Enumerate ``<publisher>/<model-dir>`` under LM Studio's models dir."""
    if not models_dir.is_dir():
        return []

    entries: list[dict] = []
    for pub_dir in sorted(models_dir.iterdir()):
        if not pub_dir.is_dir():
            continue
        for model_dir in sorted(pub_dir.iterdir()):
            if not model_dir.is_dir():
                continue

            pieces: list[dict] = []
            partial: list[str] = []
            total_bytes = 0

            for p in sorted(model_dir.iterdir()):
                if not p.is_file():
                    continue
                if p.name in SKIP_FILENAMES:
                    continue
                if p.name.endswith(".part"):
                    partial.append(p.name)
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                role = classify(p)
                pieces.append(
                    {
                        "role": role,
                        "filename": p.name,
                        "path": str(p.resolve()),
                        "bytes": size,
                    }
                )
                total_bytes += size

            pieces.sort(key=lambda piece: (_role_sort_key(piece["role"]), piece["filename"]))

            entries.append(
                {
                    "name": f"{pub_dir.name}/{model_dir.name}",
                    "publisher": pub_dir.name,
                    "directory": str(model_dir.resolve()),
                    "total_bytes": total_bytes,
                    "pieces": pieces,
                    "notes": _summarize_notes(pieces, tuple(partial)),
                }
            )

    return entries


# ---------------------------------------------------------------------------
# YAML emitter (hand-rolled to avoid an external dep)
# ---------------------------------------------------------------------------


_YAML_SPECIAL = set(":#-?{}[]&*!|>'\"%@`,()")


def _yaml_scalar(s: str) -> str:
    """Quote a string if it has YAML-special characters; else return bare."""
    if s == "":
        return '""'
    if any(c in _YAML_SPECIAL for c in s) or s.startswith((" ", "\t")) or s.endswith((" ", "\t")):
        # Use double quotes; escape backslashes and double quotes.
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _emit_entry(lines: list[str], entry: dict, indent: int) -> None:
    pad = " " * indent
    sub = " " * (indent + 2)
    lines.append(f"{pad}- name: {_yaml_scalar(entry['name'])}")
    for key in ("snapshot", "publisher", "directory"):
        if entry.get(key):
            lines.append(f"{sub}{key}: {_yaml_scalar(entry[key])}")
    lines.append(f"{sub}total_bytes: {entry['total_bytes']}")

    if entry.get("notes"):
        lines.append(f"{sub}notes:")
        for n in entry["notes"]:
            lines.append(f"{sub}  - {_yaml_scalar(n)}")

    if not entry["pieces"]:
        lines.append(f"{sub}pieces: []")
        return

    lines.append(f"{sub}pieces:")
    for piece in entry["pieces"]:
        lines.append(f"{sub}  - role: {piece['role']}")
        lines.append(f"{sub}    filename: {_yaml_scalar(piece['filename'])}")
        lines.append(f"{sub}    path: {_yaml_scalar(piece['path'])}")
        lines.append(f"{sub}    bytes: {piece['bytes']}")


def emit_yaml(data: dict) -> str:
    lines: list[str] = []
    lines.append(f"# Model catalog generated {data['generated_at']}")
    lines.append(f"# root: {data['root']}")
    lines.append(f"root: {_yaml_scalar(data['root'])}")
    lines.append(f"generated_at: {data['generated_at']}")
    lines.append("")
    lines.append("huggingface:")
    if data["huggingface"]:
        for entry in data["huggingface"]:
            _emit_entry(lines, entry, indent=2)
    else:
        lines.append("  []")
    lines.append("")
    lines.append("lmstudio:")
    if data["lmstudio"]:
        for entry in data["lmstudio"]:
            _emit_entry(lines, entry, indent=2)
    else:
        lines.append("  []")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown emitter
# ---------------------------------------------------------------------------


def _format_size(n: int) -> str:
    """Render a byte count as a short, human-readable string."""
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    f = float(n)
    for unit in units:
        if f < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(f)} {unit}"
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} {units[-1]}"  # unreachable


def _summarize_pieces(pieces: list[dict]) -> str:
    """One-line role summary, ignoring configs: ``main (3), vae, mmproj``."""
    counts: dict[str, int] = {}
    for p in pieces:
        if p["role"] == "config":
            continue
        counts[p["role"]] = counts.get(p["role"], 0) + 1
    if not counts:
        return "\u2014"
    items = sorted(counts.items(), key=lambda kv: _role_sort_key(kv[0]))
    parts = []
    for role, n in items:
        parts.append(f"{role} ({n})" if n > 1 else role)
    return ", ".join(parts)


def _short_sha(sha: str | None) -> str:
    return sha[:12] if sha else ""


def _emit_markdown_entry(lines: list[str], entry: dict, *, show_publisher: bool) -> None:
    size = _format_size(entry["total_bytes"])
    lines.append(f"### `{entry['name']}` \u2014 {size}")
    lines.append("")
    if show_publisher and entry.get("publisher"):
        lines.append(f"- **Publisher:** `{entry['publisher']}`")
    if entry.get("snapshot"):
        lines.append(f"- **Snapshot:** `{_short_sha(entry['snapshot'])}`")
    lines.append(f"- **Components:** {_summarize_pieces(entry['pieces'])}")
    lines.append(f"- **Directory:** `{entry['directory']}`")
    for n in entry.get("notes", []):
        lines.append(f"- \u26a0\ufe0f _{n}_")
    lines.append("")


def emit_markdown(data: dict) -> str:
    hf = data["huggingface"]
    lms = data["lmstudio"]
    hf_total = sum(e["total_bytes"] for e in hf)
    lms_total = sum(e["total_bytes"] for e in lms)
    grand_total = hf_total + lms_total

    lines: list[str] = []
    lines.append("# Model Catalog")
    lines.append("")
    lines.append(f"- **Root:** `{data['root']}`")
    lines.append(f"- **Generated:** {data['generated_at']}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Source | Count | Total Size |")
    lines.append("|--------|------:|-----------:|")
    lines.append(f"| HuggingFace | {len(hf)} | {_format_size(hf_total)} |")
    lines.append(f"| LM Studio | {len(lms)} | {_format_size(lms_total)} |")
    lines.append(f"| **All** | **{len(hf) + len(lms)}** | **{_format_size(grand_total)}** |")
    lines.append("")

    # Largest first -- the heavy models are what you scan for.
    lines.append(f"## HuggingFace ({len(hf)} repo{'s' if len(hf) != 1 else ''})")
    lines.append("")
    if hf:
        for entry in sorted(hf, key=lambda e: -e["total_bytes"]):
            _emit_markdown_entry(lines, entry, show_publisher=False)
    else:
        lines.append("_No HuggingFace repos found._")
        lines.append("")

    lines.append(f"## LM Studio ({len(lms)} model{'s' if len(lms) != 1 else ''})")
    lines.append("")
    if lms:
        for entry in sorted(lms, key=lambda e: -e["total_bytes"]):
            _emit_markdown_entry(lines, entry, show_publisher=True)
    else:
        lines.append("_No LM Studio models found._")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a YAML catalog of HuggingFace and LM Studio models."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Root directory containing huggingface/ and lmstudio/ (default: cwd).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="MODEL_CATALOG.yaml",
        help="Output YAML path (default: MODEL_CATALOG.yaml in cwd).",
    )
    parser.add_argument(
        "--no-markdown",
        action="store_true",
        help="Skip writing the companion .md summary file.",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    hub_dir = root / "huggingface" / "hub"
    lms_dir = root / "lmstudio" / "models"

    hf_entries = walk_huggingface(hub_dir)
    lms_entries = walk_lmstudio(lms_dir)

    data = {
        "root": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "huggingface": hf_entries,
        "lmstudio": lms_entries,
    }

    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()
    if _write_if_changed(output, emit_yaml(data)):
        print(f"wrote {output}", file=sys.stderr)
    else:
        print(f"unchanged {output}", file=sys.stderr)
    print(
        f"  {len(hf_entries)} HuggingFace repos, {len(lms_entries)} LM Studio models",
        file=sys.stderr,
    )

    if not args.no_markdown:
        md_output = output.with_suffix(".md")
        if _write_if_changed(md_output, emit_markdown(data)):
            print(f"wrote {md_output}", file=sys.stderr)
        else:
            print(f"unchanged {md_output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())