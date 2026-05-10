from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from api import queries
from api.schemas import SharedDynamicRow

router = APIRouter(prefix="/api/novels/{novel_id}/dynamics", tags=["dynamics"])


@router.get("", response_model=list[SharedDynamicRow])
def list_shared_dynamics(novel_id: UUID, cap: int | None = None):
    return queries.list_shared_dynamics(novel_id, cap)
