from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from api import admin
from api.schemas import NovelCreate, NovelSummary
from reads import novels as novels_reads
from reads.db import get_db

router = APIRouter(prefix="/api/novels", tags=["novels"])


@router.get("", response_model=list[NovelSummary])
def list_novels() -> list[NovelSummary]:
    return [NovelSummary(**row) for row in novels_reads.list_novels(get_db())]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=NovelSummary)
def create_novel(body: NovelCreate) -> NovelSummary:
    if not body.title or not body.title.strip():
        raise HTTPException(status_code=422, detail="title must not be blank")
    custom_types = [{"name": t.name, "description": t.description} for t in body.custom_entity_types]
    row = admin.create_novel(body.title.strip(), body.author, body.language, custom_types)
    return NovelSummary(**row)


@router.get("/{novel_id}", response_model=NovelSummary)
def get_novel(novel_id: UUID) -> NovelSummary:
    row = novels_reads.get_novel(get_db(), novel_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Novel not found")
    return NovelSummary(**row)


@router.delete("/{novel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_novel(novel_id: UUID) -> None:
    if not admin.delete_novel(novel_id):
        raise HTTPException(status_code=404, detail="Novel not found")
