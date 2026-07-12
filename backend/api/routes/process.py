from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from api.jobs import get_job, submit_generation_job, submit_job
from api.schemas import GenerateRequest, JobStatusResponse, ProcessRequest
from reads import chapters as chapters_reads
from reads import novels as novels_reads
from reads.db import get_db

router = APIRouter(tags=["process"])


@router.post(
    "/api/novels/{novel_id}/chapters/process",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=dict,
)
def process_chapter(novel_id: UUID, body: ProcessRequest) -> dict:
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty")

    novel = novels_reads.get_novel(get_db(), novel_id)
    if novel is None:
        raise HTTPException(status_code=404, detail="Novel not found")

    job_id = submit_job(
        novel_id=str(novel_id),
        chapter_number=body.number,
        text=body.text,
        replace=body.replace,
    )
    return {"job_id": job_id}


@router.post(
    "/api/novels/{novel_id}/chapters/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=dict,
)
def generate_chapter_endpoint(novel_id: UUID, body: GenerateRequest) -> dict:
    novel = novels_reads.get_novel(get_db(), novel_id)
    if novel is None:
        raise HTTPException(status_code=404, detail="Novel not found")
    # Fail before paying for the plan/draft/critique LLM calls: generation
    # ingests with replace=False, so a duplicate chapter number would only
    # error minutes later at ingest time and discard the finished draft.
    existing = {
        c.get("number") for c in chapters_reads.list_chapters(get_db(), novel_id, None)
    }
    if body.number in existing:
        raise HTTPException(
            status_code=409,
            detail=f"Chapter {body.number} already exists; generation does not overwrite chapters",
        )
    job_id = submit_generation_job(
        novel_id=str(novel_id), chapter_number=body.number, ingest=body.ingest
    )
    return {"job_id": job_id}


@router.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    record = get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        job_id=record.job_id,
        status=record.status,
        current_pass=record.current_pass,
        passes_done=record.passes_done,
        total_passes=record.total_passes,
        result=record.result,
        error=record.error,
    )
