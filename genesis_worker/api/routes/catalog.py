"""Catalog view — full list, by-source grouping, and single-entry lookup."""

from fastapi import APIRouter, HTTPException

from ..deps import WorkerDep
from ..schemas import CatalogBySourceSchema, CatalogSchema, ModelEntrySchema

router = APIRouter()


@router.get("/catalog", response_model=CatalogSchema)
def get_catalog(worker: WorkerDep) -> CatalogSchema:
    return CatalogSchema.from_dataclass(worker.catalog())


@router.get("/catalog/by-source", response_model=CatalogBySourceSchema)
def get_catalog_by_source(worker: WorkerDep) -> CatalogBySourceSchema:
    catalog = worker.catalog()
    return CatalogBySourceSchema(
        generated_at=catalog.generated_at,
        content_hash=catalog.content_hash,
        by_source={
            src: [ModelEntrySchema.from_dataclass(e) for e in entries]
            for src, entries in catalog.by_source().items()
        },
    )


@router.get("/catalog/{source}/{name:path}", response_model=ModelEntrySchema)
def get_catalog_entry(source: str, name: str, worker: WorkerDep) -> ModelEntrySchema:
    for entry in worker.catalog().entries:
        if entry.source == source and entry.name == name:
            return ModelEntrySchema.from_dataclass(entry)
    raise HTTPException(status_code=404, detail=f"no model {source}/{name}")


__all__ = ["router"]
