from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api.schemas import KnowsEdgeRow, LocationEdgeRow, PossessionEdgeRow
from reads import knowledge as knowledge_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}", tags=["knowledge"])


@router.get("/knows", response_model=list[KnowsEdgeRow])
def list_knows(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    character_id: UUID | None = Query(default=None),
) -> list[KnowsEdgeRow]:
    return [
        KnowsEdgeRow(**r)
        for r in knowledge_reads.list_knows_edges(get_db(), novel_id, cap, character_id)
    ]


@router.get("/locations-history", response_model=list[LocationEdgeRow])
def list_locations_history(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    only_active: bool = Query(default=False),
) -> list[LocationEdgeRow]:
    return [
        LocationEdgeRow(**r)
        for r in knowledge_reads.list_location_edges(get_db(), novel_id, cap, only_active)
    ]


@router.get("/possessions", response_model=list[PossessionEdgeRow])
def list_possessions(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    only_active: bool = Query(default=False),
) -> list[PossessionEdgeRow]:
    return [
        PossessionEdgeRow(**r)
        for r in knowledge_reads.list_possession_edges(get_db(), novel_id, cap, only_active)
    ]
