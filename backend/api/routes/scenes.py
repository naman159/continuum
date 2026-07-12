from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api.schemas import SceneRow
from reads import chapters as chapters_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/scenes", tags=["scenes"])


@router.get("", response_model=list[SceneRow])
def list_scenes(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    chapter: int | None = Query(default=None, description="Filter to a single chapter number"),
) -> list[SceneRow]:
    return [SceneRow(**r) for r in chapters_reads.list_scenes(get_db(), novel_id, cap, chapter)]
