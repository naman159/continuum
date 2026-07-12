from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api.schemas import SearchResults
from reads import search as search_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/search", tags=["search"])


@router.get("", response_model=SearchResults)
def search_novel(
    novel_id: UUID,
    q: str = Query(..., min_length=1),
    cap: int | None = Query(default=None),
    k: int = Query(default=8, ge=1, le=50),
) -> SearchResults:
    result = search_reads.search(get_db(), novel_id, q, cap, k=k)
    return SearchResults(**result)
