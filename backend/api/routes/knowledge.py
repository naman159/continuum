from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import KnowsEdgeRow, LocationEdgeRow, PossessionEdgeRow

router = APIRouter(prefix="/api/novels/{novel_id}", tags=["knowledge"])


@router.get("/knows", response_model=list[KnowsEdgeRow])
def list_knows(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    character_id: UUID | None = Query(default=None),
) -> list[KnowsEdgeRow]:
    return [
        KnowsEdgeRow(**r)
        for r in queries.list_knows_edges(novel_id, cap, character_id)
    ]


@router.get("/locations-history", response_model=list[LocationEdgeRow])
def list_locations_history(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    only_active: bool = Query(default=False),
) -> list[LocationEdgeRow]:
    return [
        LocationEdgeRow(**r)
        for r in queries.list_location_edges(novel_id, cap, only_active)
    ]


@router.get("/possessions", response_model=list[PossessionEdgeRow])
def list_possessions(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    only_active: bool = Query(default=False),
) -> list[PossessionEdgeRow]:
    return [
        PossessionEdgeRow(**r)
        for r in queries.list_possession_edges(novel_id, cap, only_active)
    ]
