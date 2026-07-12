from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api.schemas import RelationshipGraph
from reads import graphs as graphs_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/relationships", tags=["relationships"])


@router.get("", response_model=RelationshipGraph)
def get_relationships(novel_id: UUID, cap: int | None = Query(default=None)) -> RelationshipGraph:
    data = graphs_reads.relationship_graph(get_db(), novel_id, cap)
    return RelationshipGraph.model_validate(data)
