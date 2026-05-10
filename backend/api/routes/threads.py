from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import PlotThread

router = APIRouter(prefix="/api/novels/{novel_id}/threads", tags=["threads"])


@router.get("", response_model=list[PlotThread])
def list_threads(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    status: Literal["open", "progressing", "closed", "all"] = Query(default="all"),
) -> list[PlotThread]:
    return [PlotThread(**row) for row in queries.list_threads(novel_id, cap, status)]
