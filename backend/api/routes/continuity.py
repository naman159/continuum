from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.schemas import ContinuityFlag, CritiqueChapterRow, CritiqueReportDetail
from reads import continuity as continuity_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/continuity", tags=["continuity"])


@router.get("", response_model=list[ContinuityFlag])
def list_continuity(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    resolved: Literal["open", "all"] = Query(default="all"),
) -> list[ContinuityFlag]:
    return [
        ContinuityFlag(**row)
        for row in continuity_reads.list_flags(get_db(), novel_id, cap, resolved)
    ]


@router.get("/critique", response_model=list[CritiqueChapterRow])
def list_critique(
    novel_id: UUID, cap: int | None = Query(default=None)
) -> list[CritiqueChapterRow]:
    return [
        CritiqueChapterRow(**row)
        for row in continuity_reads.list_critiques(get_db(), novel_id, cap)
    ]


@router.get("/critique/{chapter_number}", response_model=CritiqueReportDetail)
def get_chapter_critique(novel_id: UUID, chapter_number: int) -> CritiqueReportDetail:
    row = continuity_reads.get_chapter_critique(get_db(), novel_id, chapter_number)
    if row is None:
        raise HTTPException(status_code=404, detail="No critique report for this chapter")
    return CritiqueReportDetail(**row)
