from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import TimelineEvent

router = APIRouter(prefix="/api/novels/{novel_id}/timeline", tags=["timeline"])


@router.get("", response_model=list[TimelineEvent])
def list_timeline(novel_id: UUID, cap: int | None = Query(default=None)) -> list[TimelineEvent]:
    return [TimelineEvent(**row) for row in queries.list_timeline(novel_id, cap)]
