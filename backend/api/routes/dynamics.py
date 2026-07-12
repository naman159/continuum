from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from api.schemas import SharedDynamicRow
from reads import graphs as graphs_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/dynamics", tags=["dynamics"])


@router.get("", response_model=list[SharedDynamicRow])
def list_shared_dynamics(novel_id: UUID, cap: int | None = None):
    return graphs_reads.list_shared_dynamics(get_db(), novel_id, cap)
