from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import CommitmentRow

router = APIRouter(prefix="/api/novels/{novel_id}/commitments", tags=["commitments"])


@router.get("", response_model=list[CommitmentRow])
def list_commitments(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    status: Literal["pending", "satisfied", "broken", "abandoned", "all"] = Query(
        default="all"
    ),
) -> list[CommitmentRow]:
    return [CommitmentRow(**r) for r in queries.list_commitments(novel_id, cap, status)]
