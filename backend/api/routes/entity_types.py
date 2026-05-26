from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from api import queries
from api.schemas import NovelEntityType, CustomEntitySummary, CustomEntityDetail
from pipeline.extraction.presets import list_genres

router = APIRouter(tags=["entity_types"])


@router.get("/api/genres")
def get_genres() -> list[dict]:
    return list_genres()


@router.get("/api/novels/{novel_id}/entity-types", response_model=list[NovelEntityType])
def get_entity_types(novel_id: UUID) -> list[NovelEntityType]:
    rows = queries.list_entity_types(novel_id)
    return [NovelEntityType(**r) for r in rows]


@router.get(
    "/api/novels/{novel_id}/entity-types/{type_name}/entities",
    response_model=list[CustomEntitySummary],
)
def list_custom_entities(novel_id: UUID, type_name: str) -> list[CustomEntitySummary]:
    rows = queries.list_custom_entities(novel_id, type_name)
    return [CustomEntitySummary(**r) for r in rows]


@router.get(
    "/api/novels/{novel_id}/custom-entities/{entity_id}",
    response_model=CustomEntityDetail,
)
def get_custom_entity(novel_id: UUID, entity_id: UUID) -> CustomEntityDetail:
    row = queries.get_custom_entity_detail(novel_id, entity_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return CustomEntityDetail(**row)
