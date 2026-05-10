from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import ContinuityFlag

router = APIRouter(prefix="/api/novels/{novel_id}/continuity", tags=["continuity"])


@router.get("", response_model=list[ContinuityFlag])
def list_continuity(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    resolved: Literal["open", "all"] = Query(default="all"),
) -> list[ContinuityFlag]:
    return [ContinuityFlag(**row) for row in queries.list_continuity(novel_id, cap, resolved)]
