"""Symlink applier for ComfyUI models — bridges the source-organised vault to ComfyUI's role-organised models dir.

The applier reads ``<config_dir>/comfyui/model_symlink.yaml`` and creates
symlinks under ``<vault>/comfyui/<target_subdir>/`` pointing at catalog
files. Catalog identity (source, entry, piece filename) is stored in the
yaml — not absolute blob paths — so HF snapshot rotations that keep the
filename intact do not break symlinks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ...contracts import Catalog

_YAML_VERSION = 1


@dataclass(frozen=True)
class SymlinkRow:
    """One yaml-stored mapping plus its on-disk resolution."""

    source: str
    entry: str
    piece: str
    target_subdir: str
    symlink_path: Path
    target_path: Path | None = None  # None when dangling


@dataclass(frozen=True)
class ApplyResult:
    created: list[SymlinkRow] = field(default_factory=list)
    updated: list[SymlinkRow] = field(default_factory=list)
    errors: list[tuple[SymlinkRow, str]] = field(default_factory=list)


@dataclass(frozen=True)
class PruneResult:
    removed: list[SymlinkRow] = field(default_factory=list)


class SymlinkApplier:
    """Manages ``<vault>/comfyui/`` symlinks backed by a yaml registry."""

    def __init__(
        self,
        *,
        symlinks_file: Path,
        vault_models_dir: Path,
    ) -> None:
        self._symlinks_file = symlinks_file
        self._vault_models_dir = vault_models_dir

    # --- yaml io -----------------------------------------------------------

    def _read_yaml_rows(self) -> list[dict[str, str]]:
        if not self._symlinks_file.is_file():
            return []
        try:
            with self._symlinks_file.open() as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return []
        if not isinstance(data, dict):
            return []
        if data.get("version") != _YAML_VERSION:
            return []
        rows = data.get("symlinks", [])
        if not isinstance(rows, list):
            return []
        out: list[dict[str, str]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            if not all(isinstance(r.get(k), str) for k in ("source", "entry", "piece", "target_subdir")):
                continue
            out.append(
                {
                    "source": r["source"],
                    "entry": r["entry"],
                    "piece": r["piece"],
                    "target_subdir": r["target_subdir"],
                }
            )
        return out

    def _write_yaml_rows(self, rows: list[dict[str, str]]) -> None:
        self._symlinks_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _YAML_VERSION, "symlinks": rows}
        tmp = self._symlinks_file.with_suffix(
            f".tmp.{os.getpid()}.{os.urandom(4).hex()}"
        )
        with tmp.open("w") as f:
            yaml.safe_dump(payload, f, sort_keys=False)
        os.replace(tmp, self._symlinks_file)

    # --- resolution --------------------------------------------------------

    def _resolve_symlink_path(self, row: dict[str, str], piece_filename: str) -> Path:
        return self._vault_models_dir / row["target_subdir"] / Path(piece_filename).name

    def _resolve_row(self, row: dict[str, str], catalog: Catalog) -> SymlinkRow:
        """Resolve a yaml row against ``catalog``. ``target_path`` is the resolved blob path.

        Returns a ``SymlinkRow`` even when the catalog piece is missing
        (target_path=None signals that). Callers may filter on this.
        """
        by_source = catalog.by_source()
        entries = by_source.get(row["source"], [])
        entry = next((e for e in entries if e.name == row["entry"]), None)
        target_path: Path | None = None
        if entry is not None:
            piece = next(
                (p for p in entry.pieces if Path(p.filename).name == Path(row["piece"]).name),
                None,
            )
            if piece is not None:
                target_path = Path(piece.path)
        return SymlinkRow(
            source=row["source"],
            entry=row["entry"],
            piece=row["piece"],
            target_subdir=row["target_subdir"],
            symlink_path=self._resolve_symlink_path(row, row["piece"]),
            target_path=target_path,
        )

    # --- public API --------------------------------------------------------

    def list_current(self, catalog: Catalog) -> list[SymlinkRow]:
        """Resolve every yaml row to its current on-disk state.

        Includes dangling rows (``target_path=None``) so the UI can
        show them. User-owned symlinks (not in the yaml) are not
        returned.
        """
        return [self._resolve_row(r, catalog) for r in self._read_yaml_rows()]

    def add(self, rows: list[dict[str, str]]) -> list[str]:
        """Append rows to the yaml; rejects duplicates and invalid entries.

        Returns a list of error messages (empty on full success). Each
        input row must have keys ``source``, ``entry``, ``piece``,
        ``target_subdir`` — all strings.
        """
        errors: list[str] = []
        existing = self._read_yaml_rows()
        seen_keys = {(r["source"], r["entry"], r["piece"]) for r in existing}

        new_rows: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                errors.append(f"invalid row (not a dict): {row!r}")
                continue
            if not all(isinstance(row.get(k), str) and row[k] for k in ("source", "entry", "piece", "target_subdir")):
                errors.append(f"invalid row: {row!r}")
                continue
            key = (row["source"], row["entry"], row["piece"])
            if key in seen_keys:
                errors.append(f"duplicate: {row}")
                continue
            seen_keys.add(key)
            new_rows.append(row)

        if new_rows:
            self._write_yaml_rows(existing + new_rows)
        return errors

    def remove(self, rows: list[SymlinkRow]) -> None:
        """Remove rows from the yaml (does not touch the symlinks on disk).

        Disk cleanup is :meth:`prune_dangling`'s job.
        """
        existing = self._read_yaml_rows()
        targets = {(r.source, r.entry, r.piece) for r in rows}
        kept = [r for r in existing if (r["source"], r["entry"], r["piece"]) not in targets]
        if len(kept) != len(existing):
            self._write_yaml_rows(kept)

    def apply(self, catalog: Catalog) -> ApplyResult:
        """Reconcile the yaml with the filesystem.

        For every yaml row: ensure the target subdir exists, then ensure
        the symlink points to the catalog piece's resolved blob path.
        Idempotent — running twice produces a no-op on the second pass.
        """
        result = ApplyResult()
        for raw in self._read_yaml_rows():
            row = self._resolve_row(raw, catalog)
            if row.target_path is None:
                result.errors.append((row, "catalog entry or piece not found"))
                continue

            row.target_subdir  # noqa: B018 — keep field referenced
            row.target_path  # noqa: B018
            target_dir = self._vault_models_dir / raw["target_subdir"]
            target_dir.mkdir(parents=True, exist_ok=True)

            symlink_path = row.symlink_path
            existing_target: Path | None = None
            if symlink_path.is_symlink():
                try:
                    existing_target = symlink_path.resolve(strict=False)
                except OSError:
                    existing_target = None
            elif symlink_path.exists():
                # A regular file lives at the symlink path; refuse to clobber.
                result.errors.append(
                    (row, f"path exists and is not a symlink: {symlink_path}")
                )
                continue

            if existing_target is not None and existing_target == row.target_path:
                # Already correct; no-op.
                continue

            if symlink_path.is_symlink() or symlink_path.exists():
                symlink_path.unlink()
            symlink_path.symlink_to(row.target_path)

            if existing_target is None:
                result.created.append(row)
            else:
                result.updated.append(row)

        return result

    def prune_dangling(self) -> PruneResult:
        """Remove symlinks whose targets don't exist; remove their yaml rows too.

        Walks ``vault_models_dir`` recursively — not just yaml-known
        symlinks — so user-deleted target files don't leave orphans.
        Symlinks not in the yaml are also removed (they're orphan
        write attempts).
        """
        result = PruneResult()

        if not self._vault_models_dir.is_dir():
            return result

        dangling_paths: set[Path] = set()
        for entry in self._vault_models_dir.rglob("*"):
            if not entry.is_symlink():
                continue
            try:
                if not entry.resolve(strict=True).exists():
                    dangling_paths.add(entry)
            except (OSError, RuntimeError):
                dangling_paths.add(entry)

        if not dangling_paths:
            return result

        # Remove from filesystem.
        for p in sorted(dangling_paths):
            try:
                p.unlink()
            except OSError:
                continue

        # Update yaml: drop rows whose symlink_path is in the dangling set.
        rows = self._read_yaml_rows()
        kept: list[dict[str, str]] = []
        for r in rows:
            sym_path = self._resolve_symlink_path(r, r["piece"])
            if sym_path in dangling_paths:
                # We need a SymlinkRow for the result, but ``piece`` is the
                # yaml-stored filename (not necessarily the resolved name).
                # Resolve to a representative row.
                result.removed.append(
                    SymlinkRow(
                        source=r["source"],
                        entry=r["entry"],
                        piece=r["piece"],
                        target_subdir=r["target_subdir"],
                        symlink_path=sym_path,
                        target_path=None,
                    )
                )
            else:
                kept.append(r)
        if len(kept) != len(rows):
            self._write_yaml_rows(kept)

        return result


__all__ = ["ApplyResult", "PruneResult", "SymlinkApplier", "SymlinkRow"]
