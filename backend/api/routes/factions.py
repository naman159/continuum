from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from api import queries
from api.schemas import FactionDetail, FactionSummary

router = APIRouter(prefix="/api/novels/{novel_id}/factions", tags=["factions"])


@router.get("", response_model=list[FactionSummary])
def list_factions(novel_id: UUID) -> list[FactionSummary]:
    return [FactionSummary(**row) for row in queries.list_factions(novel_id)]


@router.get("/{faction_id}", response_model=FactionDetail)
def get_faction(novel_id: UUID, faction_id: UUID) -> FactionDetail:
    detail = queries.get_faction_detail(novel_id, faction_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Faction not found")
    return FactionDetail(**detail)
