from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api.schemas import ChapterEvent
from reads import timeline as timeline_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/timeline", tags=["timeline"])


@router.get("", response_model=list[ChapterEvent])
def list_timeline(novel_id: UUID, cap: int | None = Query(default=None)) -> list[ChapterEvent]:
    return [ChapterEvent(**row) for row in timeline_reads.list_timeline(get_db(), novel_id, cap)]
