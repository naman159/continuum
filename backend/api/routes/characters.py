from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api import queries
from api.schemas import CharacterDetail, CharacterSummary

router = APIRouter(prefix="/api/novels/{novel_id}/characters", tags=["characters"])


@router.get("", response_model=list[CharacterSummary])
def list_characters(novel_id: UUID, cap: int | None = Query(default=None)) -> list[CharacterSummary]:
    return [CharacterSummary(**row) for row in queries.list_characters(novel_id, cap)]


@router.get("/{character_id}", response_model=CharacterDetail)
def get_character(
    novel_id: UUID,
    character_id: UUID,
    cap: int | None = Query(default=None),
) -> CharacterDetail:
    detail = queries.get_character_detail(novel_id, character_id, cap)
    if detail is None:
        raise HTTPException(status_code=404, detail="Character not found")
    return CharacterDetail(**detail)
