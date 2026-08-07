#!/usr/bin/env python3
"""Build llama-swap config.yaml from MODEL_CATALOG.yaml + recipes.yaml.

Walks the catalog and emits one llama-swap model entry per
(catalog-entry, matched-recipe) pair. Recipe matching is by simple
substring keyword on the model name (normalized: lowercase + strip
hyphens/underscores/dots). Substring shadowing drops recipes whose
keyword is a substring of another matched keyword. Recipes sharing a
keyword are siblings — all emitted as alternatives.

Resource policy is recipe-driven; context is left to llama.cpp's --fit
(--fit-ctx is set only when a recipe declares ctx_min).

Usage:
    ./build_config.py
    ./build_config.py --catalog MODEL_CATALOG.yaml
    ./build_config.py --recipes recipes.yaml
    ./build_config.py -o config.generated.yaml
    ./build_config.py --binary /path/to/llama-server
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


# Resolve repo root so recipes can use paths like "vendor/llama.cpp/build/bin/llama-server".
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent

# Default llama-server path (relative to REPO_ROOT). Recipes can override
# per family. Bonsai overrides to the PrismML fork because its ternary
# Q2_0 weights need the custom kernels.
DEFAULT_BINARY_REL = "vendor/llama.cpp/build/bin/llama-server"

# Resource policy thresholds (bytes). When a model's weight size exceeds
# one of these, the corresponding VRAM-saver flag is added. These are
# machine-dependent (would change if you swapped GPUs), not model-
# dependent, so they live in code rather than in recipes.yaml.
KV_QUANT_OVER = 25_000_000_000       # add -ctk q8_0 -ctv q8_0 above this
MMPROJ_OFFLOAD_OVER = 25_000_000_000 # add --no-mmproj-offload above this


def _write_if_changed(path: Path, payload: str) -> bool:
    """Write ``payload`` to ``path`` only if the existing contents differ.

    Preserving the existing mtime when the content is unchanged is what
    keeps llama-swap's -watch-config from reloading the registry (and
    killing any in-flight request) on a no-op rebuild. Returns True when
    a write actually happened.
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


def _opt(recipe: dict, default_recipe: dict | None, key: str):
    """Resolve an option: recipe's value if set, else default recipe's,
    else None. Used so the `default` recipe in recipes.yaml acts as a
    single source of truth for shared knobs (ctx_min, kv_quant_over,
    parallel, sampling, etc.)."""
    if key in recipe and recipe[key] is not None:
        return recipe[key]
    if default_recipe and key in default_recipe and default_recipe[key] is not None:
        return default_recipe[key]
    return None


def _resolve_binary(binary: str) -> str:
    """Resolve a binary path: relative paths are joined with REPO_ROOT,
    absolute paths are used as-is. Returns an absolute, normalized path."""
    p = Path(binary)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return str(p.resolve())


# ---------------------------------------------------------------------------
# Recipe matching
# ---------------------------------------------------------------------------


def normalize(s: str) -> str:
    """Lowercase + strip hyphens/underscores/dots. Stripping dots lets
    "qwen3.6" match "Qwen3.6-35B-A3B" after normalization; qwen3.5 and
    qwen3.6 still stay distinct because the digits differ."""
    return s.lower().replace("-", "").replace("_", "").replace(".", "")


def get_matching_recipes(model_name: str, recipes: list[dict]) -> list[dict]:
    """Recipes whose normalized keyword is a substring of the normalized
    model name. Among distinct keywords, only the longest applies
    (substring shadowing). Recipes sharing a keyword are siblings and
    are all returned as alternatives."""
    base = model_name.split("/", 1)[-1] if "/" in model_name else model_name
    norm_model = normalize(base)

    matched: list[tuple[dict, str]] = []
    for recipe in recipes:
        kw_field = (recipe.get("match") or "").strip()
        if not kw_field:
            continue
        kw = normalize(kw_field)
        if kw and kw in norm_model:
            matched.append((recipe, kw))

    if not matched:
        return []

    by_kw: dict[str, list[dict]] = {}
    for recipe, kw in matched:
        by_kw.setdefault(kw, []).append(recipe)

    keep: set[str] = set()
    for kw in sorted(by_kw.keys(), key=len, reverse=True):
        if not any(kw in kept for kept in keep):
            keep.add(kw)

    return [r for kw in by_kw if kw in keep for r in by_kw[kw]]


# ---------------------------------------------------------------------------
# File auto-detection
# ---------------------------------------------------------------------------


def _has_gguf(pieces: list[dict]) -> bool:
    return any(p["filename"].lower().endswith(".gguf") for p in pieces)


def _is_llm_candidate(entry: dict, source: str) -> bool:
    """Skip non-LLMs: image-gen / adapters / safetensors-only HF / empty
    downloads. LMS entries with only chat templates are also dropped."""
    notes = entry.get("notes", []) or []
    if any("no model weights on disk" in n for n in notes):
        return False
    pieces = entry.get("pieces", [])
    if not pieces:
        return False
    if source == "huggingface":
        return _has_gguf(pieces)
    return any(p["role"] not in ("config",) for p in pieces)


def detect_files(pieces: list[dict], total_bytes: int, model_name: str) -> dict:
    """Pick main / mmproj / draft paths from a catalog entry's pieces.
    For "main" we choose the largest piece (handles Q4+Q6 siblings in
    the same repo by preferring the bigger quant)."""
    mains = [p for p in pieces if p["role"] == "main"]
    mmprojs = [p for p in pieces if p["role"] == "mmproj"]
    drafts = [p for p in pieces if p["role"] == "mtp"]
    main_path = max(mains, key=lambda p: p["bytes"])["path"] if mains else None
    has_mtp = (
        bool(drafts)
        or "mtp" in model_name.lower()
        or any("mtp" in p["filename"].lower() for p in mains)
    )
    return {
        "main": main_path,
        "mmproj": mmprojs[0]["path"] if mmprojs else None,
        "draft": drafts[0]["path"] if drafts else None,
        "is_mtp": has_mtp,
        "weight_bytes": total_bytes,
    }


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------


_SAMPLING_FLAGS = {
    "temp": "--temp",
    "top_p": "--top-p",
    "top_k": "--top-k",
    "min_p": "--min-p",
    "presence_penalty": "--presence-penalty",
    "repeat_penalty": "--repeat-penalty",
}


def build_cmd(
    recipe: dict,
    files: dict,
    *,
    binary: str | None = None,
    default_recipe: dict | None = None,
) -> str:
    """Compose the llama-server command line for one model entry.
    Returns a multi-line shell string with backslash continuations.
    Options not set on `recipe` fall back to `default_recipe`.

    Binary resolution order: recipe.binary -> CLI --binary ->
    default.binary -> DEFAULT_BINARY_REL (relative to REPO_ROOT).
    The CLI flag beats default so a per-invocation override (e.g. pointing
    at a dev build) wins over the committed default."""
    binary_str = (
        recipe.get("binary")
        or binary
        or (default_recipe.get("binary") if default_recipe else None)
        or DEFAULT_BINARY_REL
    )
    resolved_binary = _resolve_binary(binary_str)

    sections: list[str] = []
    sections.append(f"{resolved_binary} \\")
    sections.append(f"  --model {files['main']} \\")

    if files.get("mmproj"):
        sections.append(f"  --mmproj {files['mmproj']} \\")
        # Recipe can force offload on/off; otherwise use the weight-size
        # threshold.
        mmproj_offload = recipe.get("mmproj_offload")
        if mmproj_offload is None and default_recipe:
            mmproj_offload = default_recipe.get("mmproj_offload")
        if mmproj_offload is True:
            offload = True
        elif mmproj_offload is False:
            offload = False
        else:
            offload = files["weight_bytes"] > MMPROJ_OFFLOAD_OVER
        if offload:
            sections.append("  --no-mmproj-offload \\")

    spec = _opt(recipe, default_recipe, "spec") or {}
    if spec.get("type") == "draft-mtp" and files.get("is_mtp"):
        spec_parts = ["--spec-type", "draft-mtp"]
        if "n_max" in spec:
            spec_parts.extend(["--spec-draft-n-max", str(spec["n_max"])])
        if "p_min" in spec:
            spec_parts.extend(["--spec-draft-p-min", str(spec["p_min"])])
        sections.append("  " + " ".join(spec_parts) + " \\")
        if files.get("draft"):
            sections.append(f"  --model-draft {files['draft']} \\")

    # Runtime / hardware knobs grouped together.
    runtime: list[str] = []
    kv_dtype = _opt(recipe, default_recipe, "kv_cache")
    if kv_dtype in ("q8_0", "q4_0"):
        runtime.extend(["-ctk", kv_dtype, "-ctv", kv_dtype])
    elif files["weight_bytes"] > KV_QUANT_OVER:
        runtime.extend(["-ctk", "q8_0", "-ctv", "q8_0"])

    ctx_min = _opt(recipe, default_recipe, "ctx_min")
    if ctx_min is not None:
        runtime.extend(["--fit-ctx", str(ctx_min)])

    parallel = _opt(recipe, default_recipe, "parallel")
    if parallel is not None:
        runtime.extend(["--parallel", str(parallel)])

    reasoning_budget = _opt(recipe, default_recipe, "reasoning_budget")
    if reasoning_budget is not None and reasoning_budget >= 0:
        runtime.extend(["--reasoning-budget", str(reasoning_budget)])

    reasoning_budget_message = _opt(recipe, default_recipe, "reasoning_budget_message")
    if reasoning_budget_message:
        # Quote so the message survives the shell as a single argv token;
        # llama-server's parser does not understand shell-style quoting.
        runtime.extend(["--reasoning-budget-message", shlex.quote(reasoning_budget_message)])

    runtime.extend(["--jinja", "-fa", "on"])
    sections.append("  " + " ".join(runtime) + " \\")

    # Port + host always on their own line.
    sections.append("  --port ${PORT} --host 127.0.0.1 \\")

    chat_template_file = _opt(recipe, default_recipe, "chat_template_file")
    if chat_template_file:
        sections.append(f"  --chat-template-file {chat_template_file} \\")

    sampling = _opt(recipe, default_recipe, "sampling") or {}
    sampling_parts: list[str] = []
    for k, flag in _SAMPLING_FLAGS.items():
        if k in sampling:
            sampling_parts.extend([flag, str(sampling[k])])
    if sampling_parts:
        sections.append("  " + " ".join(sampling_parts) + " \\")

    ctk = _opt(recipe, default_recipe, "chat_template_kwargs")
    if ctk:
        ctk_json = json.dumps(ctk, separators=(",", ":"))
        sections.append(f"  --chat-template-kwargs '{ctk_json}'")

    # Last section must NOT end with a backslash.
    if sections and sections[-1].endswith(" \\"):
        sections[-1] = sections[-1][:-2]

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Entry ID + display name
# ---------------------------------------------------------------------------


def make_entry_id(
    name: str,
    recipe: dict,
    *,
    multi_match: bool,
    all_ids: set[str],
    source: str,
) -> str:
    """Sanitize catalog name → YAML key. With multi_match, append the
    last token of the recipe name as a variant suffix. On collision,
    try publisher prefix first (differentiates same-model from different
    publishers), then source suffix (differentiates HF cache from LMS),
    then a counter."""
    base = name.split("/", 1)[-1]
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    if multi_match:
        suffix = recipe["name"].split(".")[-1].split("-")[-1]
        base = f"{base}-{suffix}"

    if base not in all_ids:
        all_ids.add(base)
        return base

    # Try publisher prefix.
    pub = name.split("/", 1)[0] if "/" in name else ""
    pub_clean = re.sub(r"[^a-z0-9]+", "-", pub.lower()).strip("-")
    if pub_clean:
        candidate = f"{pub_clean}-{base}"
        if candidate not in all_ids:
            all_ids.add(candidate)
            return candidate

    # Fall back to source suffix (HF cache vs LMS).
    src_short = "hf" if source == "huggingface" else "lms"
    candidate = f"{base}-{src_short}"
    if candidate not in all_ids:
        all_ids.add(candidate)
        return candidate

    # Last resort: counter.
    n = 2
    while f"{base}-{n}" in all_ids:
        n += 1
    candidate = f"{base}-{n}"
    all_ids.add(candidate)
    return candidate


def make_display_name(name: str, recipe: dict, multi_match: bool) -> str:
    """Display name. With multi_match, append the recipe variant."""
    base = name.split("/", 1)[-1]
    if multi_match:
        suffix = recipe["name"].split(".")[-1].split("-")[-1]
        return f"{base} ({suffix})"
    return base


# ---------------------------------------------------------------------------
# Entry construction
# ---------------------------------------------------------------------------


def build_entry(
    catalog_entry: dict,
    recipe: dict,
    *,
    source: str,
    binary: str | None,
    all_ids: set[str],
    multi_match: bool,
    default_recipe: dict | None = None,
) -> tuple[str, dict]:
    name = catalog_entry["name"]
    files = detect_files(
        catalog_entry.get("pieces", []),
        catalog_entry.get("total_bytes", 0),
        name,
    )
    entry_id = make_entry_id(
        name, recipe, multi_match=multi_match,
        all_ids=all_ids, source=source,
    )
    display = make_display_name(name, recipe, multi_match)
    cmd = build_cmd(recipe, files, binary=binary, default_recipe=default_recipe)
    return entry_id, {
        "name": display,
        "cmd": cmd,
        "proxy": "http://127.0.0.1:${PORT}",
        "ttl": 0,
    }


# ---------------------------------------------------------------------------
# YAML emission (hand-rolled, no external dep)
# ---------------------------------------------------------------------------


def emit_yaml(
    entries: list[tuple[str, dict]],
    root: str,
    generated_at: str,
) -> str:
    lines: list[str] = []
    lines.append(f"# llama-swap config generated {generated_at}")
    lines.append(f"# root: {root}")
    lines.append("# Generated by build_config.py. Edit recipes.yaml and re-run;")
    lines.append("# manual edits to this file will be overwritten.")
    lines.append("")
    lines.append("healthCheckTimeout: 60")
    lines.append("logLevel: info")
    lines.append("")
    lines.append("models:")
    for entry_id, entry in entries:
        lines.append(f"  {entry_id}:")
        lines.append(f'    name: "{entry["name"]}"')
        lines.append("    cmd: |")
        for cmd_line in entry["cmd"].split("\n"):
            lines.append("      " + cmd_line)
        lines.append(f'    proxy: "{entry["proxy"]}"')
        lines.append(f"    ttl: {entry['ttl']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build llama-swap config.yaml from MODEL_CATALOG.yaml + recipes.yaml."
        )
    )
    parser.add_argument(
        "--catalog",
        default="MODEL_CATALOG.yaml",
        help="Path to MODEL_CATALOG.yaml (default: cwd).",
    )
    parser.add_argument(
        "--recipes",
        default="recipes.yaml",
        help="Path to recipes.yaml (default: cwd).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="config.yaml",
        help="Output path (default: config.yaml in cwd).",
    )
    parser.add_argument(
        "--binary",
        default=None,
        help=(
            "Override the default llama-server path. Resolution order: "
            "recipe.binary -> default.binary -> this flag -> "
            f"{DEFAULT_BINARY_REL}."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the list of generated entries to stderr and skip writing.",
    )
    args = parser.parse_args()

    catalog = yaml.safe_load(Path(args.catalog).read_text())
    recipes_doc = yaml.safe_load(Path(args.recipes).read_text())
    recipes_dict = recipes_doc.get("recipes", {})

    matchable: list[dict] = []
    default_recipe: dict | None = None
    for rname, recipe in recipes_dict.items():
        with_name = {"name": rname, **recipe}
        if "match" not in with_name or not str(with_name["match"]).strip():
            default_recipe = with_name
        else:
            matchable.append(with_name)

    entries: list[tuple[str, dict]] = []
    all_ids: set[str] = set()
    skipped = 0
    matched_counts: dict[str, int] = {}

    for source_key in ("huggingface", "lmstudio"):
        for entry in catalog.get(source_key, []):
            if not _is_llm_candidate(entry, source_key):
                skipped += 1
                continue
            matched = get_matching_recipes(entry["name"], matchable)
            if not matched:
                if default_recipe is None:
                    skipped += 1
                    continue
                matched = [default_recipe]
                matched_counts["default"] = matched_counts.get("default", 0) + 1
            else:
                for r in matched:
                    matched_counts[r["name"]] = matched_counts.get(r["name"], 0) + 1

            multi = len(matched) > 1
            for recipe in matched:
                entry_id, data = build_entry(
                    entry, recipe, source=source_key,
                    binary=args.binary, all_ids=all_ids, multi_match=multi,
                    default_recipe=default_recipe,
                )
                entries.append((entry_id, data))

    if args.list:
        for eid, _ in entries:
            print(eid, file=sys.stderr)
        return 0

    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()
    payload = emit_yaml(
        entries,
        catalog.get("root", ""),
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    if _write_if_changed(output, payload):
        print(f"wrote {output}", file=sys.stderr)
    else:
        print(f"unchanged {output}", file=sys.stderr)
    print(f"  {len(entries)} entries emitted, {skipped} skipped", file=sys.stderr)
    print("  recipe matches:", file=sys.stderr)
    for name in sorted(matched_counts):
        print(f"    {name}: {matched_counts[name]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())