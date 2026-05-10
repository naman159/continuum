from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import ChapterSummary

router = APIRouter(prefix="/api/novels/{novel_id}/chapters", tags=["chapters"])


@router.get("", response_model=list[ChapterSummary])
def list_chapters(novel_id: UUID, cap: int | None = Query(default=None)) -> list[ChapterSummary]:
    return [ChapterSummary(**row) for row in queries.list_chapters(novel_id, cap)]
