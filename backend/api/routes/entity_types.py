from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.schemas import (
    CustomEntityDetail,
    CustomEntitySummary,
    EntityMergeRequest,
    NovelEntityType,
)
from pipeline.db.client import DBClient
from pipeline.db.entity_merge import EntityMergeError, merge_entities
from pipeline.extraction.presets import list_genres
from reads import world as world_reads
from reads.db import get_db

router = APIRouter(tags=["entity_types"])


@router.get("/api/genres")
def get_genres() -> list[dict]:
    return list_genres()


@router.get("/api/novels/{novel_id}/entity-types", response_model=list[NovelEntityType])
def get_entity_types(novel_id: UUID) -> list[NovelEntityType]:
    rows = world_reads.list_entity_types(get_db(), novel_id)
    return [NovelEntityType(**r) for r in rows]


@router.get(
    "/api/novels/{novel_id}/entity-types/{type_name}/entities",
    response_model=list[CustomEntitySummary],
)
def list_custom_entities(
    novel_id: UUID, type_name: str, cap: int | None = Query(default=None)
) -> list[CustomEntitySummary]:
    rows = world_reads.list_custom_entities(get_db(), novel_id, type_name, cap)
    return [CustomEntitySummary(**r) for r in rows]


@router.get(
    "/api/novels/{novel_id}/custom-entities/{entity_id}",
    response_model=CustomEntityDetail,
)
def get_custom_entity(
    novel_id: UUID, entity_id: UUID, cap: int | None = Query(default=None)
) -> CustomEntityDetail:
    row = world_reads.get_custom_entity_detail(get_db(), novel_id, entity_id, cap)
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return CustomEntityDetail(**row)


def _merge_db() -> DBClient:
    """Separate factory so tests can stub the merge connection."""
    return DBClient()


@router.post("/api/novels/{novel_id}/entities/merge")
def merge_novel_entities(novel_id: UUID, body: EntityMergeRequest) -> dict:
    db = _merge_db()
    try:
        return merge_entities(
            db,
            novel_id=str(novel_id),
            source_entity_id=str(body.source_entity_id),
            target_entity_id=str(body.target_entity_id),
        )
    except EntityMergeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if hasattr(db, "close"):
            db.close()
