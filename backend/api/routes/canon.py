from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import CanonFactRow

router = APIRouter(prefix="/api/novels/{novel_id}/canon", tags=["canon"])


@router.get("", response_model=list[CanonFactRow])
def list_canon_facts(
    novel_id: UUID,
    locked_only: bool = Query(default=False),
) -> list[CanonFactRow]:
    return [CanonFactRow(**r) for r in queries.list_canon_facts(novel_id, locked_only)]
