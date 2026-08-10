"""Shared role classification for model files.

Both the HuggingFace walker and the LM Studio walker classify files the
same way: ``mmproj`` and ``mtp-*.gguf`` by filename, component dirs
(``text_encoder/``, ``transformer/``, ``vae/``, ...) by parent directory,
and everything else as ``main`` (if a weight extension) or ``config``.

Lifted verbatim from ``bin/catalog.py`` — kept as a shared helper so a
bug fix is one place, not two.
"""

from __future__ import annotations

from pathlib import Path

# Recognized component subdirectory names inside an HF snapshot.
# A weight file under one of these is given the component name as its role
# (e.g. transformer/diffusion_pytorch_model.safetensors -> role: transformer).
COMPONENT_DIRS: set[str] = {
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
WEIGHT_EXTS: set[str] = {
    ".gguf",
    ".safetensors",
    ".bin",
    ".pt",
    ".pth",
    ".ckpt",
}

# Filenames we ignore when scanning snapshots / model dirs.
SKIP_FILENAMES: set[str] = {
    ".gitattributes",
    "README.md",
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "NOTICE.txt",
    "USE_POLICY.md",
    ".DS_Store",
}


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


def role_sort_key(role: str) -> tuple[int, str]:
    """Sort pieces with main weights first, configs last."""
    # main weights first, then component weights, then aux (mmproj, mtp),
    # then configs.
    order = {
        "main": 0,
        "transformer": 1,
        "unet": 1,
        "denoiser": 1,
        "text_encoder": 2,
        "image_encoder": 2,
        "encoder": 2,
        "decoder": 2,
        "vae": 3,
        "prior": 3,
        "controlnet": 3,
        "scheduler": 3,
        "mmproj": 4,
        "mtp": 5,
        "config": 6,
    }
    return (order.get(role, 9), role)


__all__ = [
    "COMPONENT_DIRS",
    "SKIP_FILENAMES",
    "WEIGHT_EXTS",
    "classify",
    "role_sort_key",
]
