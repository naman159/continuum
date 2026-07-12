from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api.schemas import EntityGraph
from reads import graphs as graphs_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/entity-graph", tags=["entity_graph"])


@router.get("", response_model=EntityGraph)
def get_entity_graph(novel_id: UUID, cap: int | None = Query(default=None)) -> EntityGraph:
    data = graphs_reads.entity_graph(get_db(), novel_id, cap)
    return EntityGraph.model_validate(data)
