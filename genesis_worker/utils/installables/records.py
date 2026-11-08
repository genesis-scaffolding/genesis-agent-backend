"""Declarative installable records and seed/override merging."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter


class InstallableBase(BaseModel):
    name: str
    label: str | None = None
    enabled: bool = True
    primary: bool = False
    variant: str | None = None
    backend: str | None = None


class GithubReleaseRecord(InstallableBase):
    kind: Literal["github_release"] = "github_release"
    repo: str
    asset_pattern: str
    binary_name: str = "llama-server"
    checksums_asset_suffix: str | None = None
    source_url: str | None = None


class DockerImageRecord(InstallableBase):
    kind: Literal["docker_image"] = "docker_image"
    image: str
    tag_pattern: str | None = None
    default_tag: str = "latest"


InstallableRecord = Annotated[
    GithubReleaseRecord | DockerImageRecord,
    Field(discriminator="kind"),
]

InstallableRecordAdapter: TypeAdapter[InstallableRecord] = TypeAdapter(InstallableRecord)


def parse_installable_record(value: object) -> InstallableRecord:
    """Validate one plain record object against the discriminated union."""
    return InstallableRecordAdapter.validate_python(value)


def merge_records(
    seeds: Sequence[InstallableRecord],
    overrides: Sequence[InstallableRecord],
) -> list[InstallableRecord]:
    """Merge seed and user records by name without mutating either input."""
    override_by_name = {record.name: record for record in overrides}
    seed_names = {record.name for record in seeds}
    merged = [override_by_name.get(record.name, record) for record in seeds]
    unknown_names = dict.fromkeys(
        record.name for record in overrides if record.name not in seed_names
    )
    merged.extend(override_by_name[name] for name in unknown_names)
    return merged


__all__ = [
    "DockerImageRecord",
    "GithubReleaseRecord",
    "InstallableBase",
    "InstallableRecord",
    "InstallableRecordAdapter",
    "merge_records",
    "parse_installable_record",
]
