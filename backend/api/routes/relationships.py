from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import RelationshipGraph

router = APIRouter(prefix="/api/novels/{novel_id}/relationships", tags=["relationships"])


@router.get("", response_model=RelationshipGraph)
def get_relationships(novel_id: UUID, cap: int | None = Query(default=None)) -> RelationshipGraph:
    data = queries.get_relationship_graph(novel_id, cap)
    return RelationshipGraph.model_validate(data)
