from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.schemas import (
    AcceptRequest,
    DraftDetail,
    DraftSummary,
    PendingCount,
    ResolveRequest,
)
from pipeline import drafts as drafts_writes
from reads import drafts as drafts_reads
from reads.db import get_db

router = APIRouter(tags=["drafts"])


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    findings = row.get("findings") or {}
    return {
        **row,
        "fail_count": len(findings.get("fails", [])),
        "warn_count": len(findings.get("warns", [])),
    }


@router.get("/api/novels/{novel_id}/drafts", response_model=list[DraftSummary])
def list_drafts(
    novel_id: UUID,
    status: Literal["pending", "accepted", "rejected", "all"] = Query(default="pending"),
) -> list[DraftSummary]:
    return [
        DraftSummary(**_summary(r))
        for r in drafts_reads.list_submissions(get_db(), novel_id, status)
    ]


@router.get("/api/novels/{novel_id}/drafts/pending-count", response_model=PendingCount)
def pending_count(novel_id: UUID) -> PendingCount:
    return PendingCount(pending=drafts_reads.count_pending(get_db(), novel_id))


@router.get("/api/drafts/{submission_id}", response_model=DraftDetail)
def get_draft(submission_id: UUID) -> DraftDetail:
    row = drafts_reads.get_submission(get_db(), submission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="draft submission not found")
    return DraftDetail(**_summary(row))


@router.post("/api/drafts/{submission_id}/accept")
def accept_draft(submission_id: UUID, body: AcceptRequest) -> dict[str, Any]:
    try:
        return drafts_writes.accept_submission(
            get_db(), str(submission_id), note=body.note, edited_text=body.edited_text
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404 if "not found" in str(exc) else 409, detail=str(exc)
        ) from exc


@router.post("/api/drafts/{submission_id}/reject")
def reject_draft(submission_id: UUID, body: ResolveRequest) -> dict[str, Any]:
    try:
        return drafts_writes.reject_submission(
            get_db(), str(submission_id), note=body.note
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404 if "not found" in str(exc) else 409, detail=str(exc)
        ) from exc
