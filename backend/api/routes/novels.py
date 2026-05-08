from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from api import queries
from api.schemas import NovelSummary

router = APIRouter(prefix="/api/novels", tags=["novels"])


@router.get("", response_model=list[NovelSummary])
def list_novels() -> list[NovelSummary]:
    return [NovelSummary(**row) for row in queries.list_novels()]


@router.get("/{novel_id}", response_model=NovelSummary)
def get_novel(novel_id: UUID) -> NovelSummary:
    row = queries.get_novel(novel_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Novel not found")
    return NovelSummary(**row)
