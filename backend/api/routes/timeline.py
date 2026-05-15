from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import ChapterEvent

router = APIRouter(prefix="/api/novels/{novel_id}/timeline", tags=["timeline"])


@router.get("", response_model=list[ChapterEvent])
def list_timeline(novel_id: UUID, cap: int | None = Query(default=None)) -> list[ChapterEvent]:
    return [ChapterEvent(**row) for row in queries.list_timeline(novel_id, cap)]
