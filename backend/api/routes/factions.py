from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.schemas import FactionDetail, FactionSummary
from reads import world as world_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/factions", tags=["factions"])


@router.get("", response_model=list[FactionSummary])
def list_factions(novel_id: UUID, cap: int | None = Query(default=None)) -> list[FactionSummary]:
    return [FactionSummary(**row) for row in world_reads.list_factions(get_db(), novel_id, cap)]


@router.get("/{faction_id}", response_model=FactionDetail)
def get_faction(
    novel_id: UUID,
    faction_id: UUID,
    cap: int | None = Query(default=None),
) -> FactionDetail:
    detail = world_reads.get_faction_detail(get_db(), novel_id, faction_id, cap)
    if detail is None:
        raise HTTPException(status_code=404, detail="Faction not found")
    return FactionDetail(**detail)
