from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import EntityGraph

router = APIRouter(prefix="/api/novels/{novel_id}/entity-graph", tags=["entity_graph"])


@router.get("", response_model=EntityGraph)
def get_entity_graph(novel_id: UUID, cap: int | None = Query(default=None)) -> EntityGraph:
    data = queries.get_entity_graph(novel_id, cap)
    return EntityGraph.model_validate(data)
