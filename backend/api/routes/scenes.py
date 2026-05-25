from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import SceneRow

router = APIRouter(prefix="/api/novels/{novel_id}/scenes", tags=["scenes"])


@router.get("", response_model=list[SceneRow])
def list_scenes(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    chapter: int | None = Query(default=None, description="Filter to a single chapter number"),
) -> list[SceneRow]:
    return [SceneRow(**r) for r in queries.list_scenes(novel_id, cap, chapter)]
