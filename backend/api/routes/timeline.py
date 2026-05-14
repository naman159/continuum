from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from api import queries
from api.schemas import TimelineEntry

router = APIRouter(prefix="/api/novels/{novel_id}/timeline", tags=["timeline"])


@router.get("", response_model=list[TimelineEntry])
def list_timeline(novel_id: UUID) -> list[TimelineEntry]:
    return [TimelineEntry(**row) for row in queries.list_timeline(novel_id)]
