from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api import queries
from api.schemas import LocationDetail, LocationSummary

router = APIRouter(prefix="/api/novels/{novel_id}/locations", tags=["locations"])


@router.get("", response_model=list[LocationSummary])
def list_locations(novel_id: UUID, cap: int | None = Query(default=None)) -> list[LocationSummary]:
    return [LocationSummary(**row) for row in queries.list_locations(novel_id, cap)]


@router.get("/{location_id}", response_model=LocationDetail)
def get_location(
    novel_id: UUID,
    location_id: UUID,
    cap: int | None = Query(default=None),
) -> LocationDetail:
    detail = queries.get_location_detail(novel_id, location_id, cap)
    if detail is None:
        raise HTTPException(status_code=404, detail="Location not found")
    return LocationDetail(**detail)
