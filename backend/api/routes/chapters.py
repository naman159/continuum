from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api.schemas import ChapterSummary
from reads import chapters as chapters_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/chapters", tags=["chapters"])


@router.get("", response_model=list[ChapterSummary])
def list_chapters(novel_id: UUID, cap: int | None = Query(default=None)) -> list[ChapterSummary]:
    return [ChapterSummary(**row) for row in chapters_reads.list_chapters(get_db(), novel_id, cap)]
