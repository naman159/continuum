from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.schemas import ObjectDetail, ObjectSummary
from reads import world as world_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/objects", tags=["objects"])


@router.get("", response_model=list[ObjectSummary])
def list_objects(novel_id: UUID, cap: int | None = Query(default=None)) -> list[ObjectSummary]:
    return [ObjectSummary(**row) for row in world_reads.list_objects(get_db(), novel_id, cap)]


@router.get("/{object_id}", response_model=ObjectDetail)
def get_object(
    novel_id: UUID,
    object_id: UUID,
    cap: int | None = Query(default=None),
) -> ObjectDetail:
    detail = world_reads.get_object_detail(get_db(), novel_id, object_id, cap)
    if detail is None:
        raise HTTPException(status_code=404, detail="Object not found")
    return ObjectDetail(**detail)
