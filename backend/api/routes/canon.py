from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from api import admin
from api.schemas import CanonFactCreate, CanonFactPatch, CanonFactRow
from reads import knowledge as knowledge_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels/{novel_id}/canon", tags=["canon"])


@router.get("", response_model=list[CanonFactRow])
def list_canon_facts(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    locked_only: bool = Query(default=False),
) -> list[CanonFactRow]:
    return [
        CanonFactRow(**r)
        for r in knowledge_reads.list_canon_facts(get_db(), novel_id, cap, locked_only)
    ]


@router.patch("/{fact_id}")
def patch_canon_fact(novel_id: UUID, fact_id: UUID, body: CanonFactPatch) -> dict:
    if body.locked is None and body.value is None:
        raise HTTPException(status_code=422, detail="nothing to update")
    ok = admin.update_canon_fact(novel_id, fact_id, locked=body.locked, value=body.value)
    if not ok:
        raise HTTPException(status_code=404, detail="Canon fact not found")
    return {"ok": True}


@router.post("", status_code=status.HTTP_201_CREATED)
def post_canon_fact(novel_id: UUID, body: CanonFactCreate) -> dict:
    row = admin.create_canon_fact(
        novel_id,
        subject_entity_id=body.subject_entity_id,
        predicate=body.predicate,
        value=body.value,
        kind=body.kind,
        locked=body.locked,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Subject entity not found in this novel")
    return {"id": str(row["id"])}


@router.delete("/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_canon_fact(novel_id: UUID, fact_id: UUID) -> None:
    if not admin.delete_canon_fact(novel_id, fact_id):
        raise HTTPException(status_code=404, detail="Canon fact not found")
