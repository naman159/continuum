from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from api.schemas import CommitmentRow
from reads import commitments as commitments_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/commitments", tags=["commitments"])


@router.get("", response_model=list[CommitmentRow])
def list_commitments(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    status: Literal["pending", "satisfied", "broken", "abandoned", "all"] = Query(
        default="all"
    ),
) -> list[CommitmentRow]:
    return [
        CommitmentRow(**r)
        for r in commitments_reads.list_commitments(get_db(), novel_id, cap, status)
    ]
