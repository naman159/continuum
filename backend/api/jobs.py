from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any


@dataclass
class JobRecord:
    job_id: str
    status: str = "pending"  # pending | running | done | error
    current_pass: str | None = None
    passes_done: int = 0
    total_passes: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None


class ProgressTracker:
    def __init__(self, *, job_id: str, store: "JobStore") -> None:
        self._job_id = job_id
        self._store = store

    def on_pass_start(self, pass_name: str) -> None:
        with self._store._lock:
            record = self._store._jobs.get(self._job_id)
            if record:
                record.status = "running"
                record.current_pass = pass_name

    def on_pass_done(self, pass_name: str) -> None:
        with self._store._lock:
            record = self._store._jobs.get(self._job_id)
            if record:
                record.passes_done += 1
                record.current_pass = None


class JobStore:
    # Finished jobs kept around for status polling; oldest are evicted past this.
    _MAX_FINISHED = 50

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def create(self, *, total_passes: int) -> str:
        job_id = str(uuid.uuid4())
        record = JobRecord(job_id=job_id, total_passes=total_passes)
        with self._lock:
            self._evict_finished_locked()
            self._jobs[job_id] = record
        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _evict_finished_locked(self) -> None:
        finished = [jid for jid, r in self._jobs.items() if r.status in ("done", "error")]
        # dicts preserve insertion order, so the front of the list is the oldest.
        for jid in finished[: max(0, len(finished) - self._MAX_FINISHED)]:
            del self._jobs[jid]

    def mark_done(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record:
                record.status = "done"
                record.result = result
                record.current_pass = None

    def mark_error(self, job_id: str, error: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record:
                record.status = "error"
                record.error = error
                record.current_pass = None


# Module-level singletons
_job_store = JobStore()
_executor = ThreadPoolExecutor(max_workers=2)


def get_job(job_id: str) -> JobRecord | None:
    return _job_store.get(job_id)


def submit_job(*, novel_id: str, chapter_number: int, text: str, replace: bool = False) -> str:
    from pipeline.config import settings
    from pipeline.extraction.chunker import sliding_window_chunks
    from pipeline.extraction.prompts import PASS_ORDER
    from pipeline.pipeline import analyze_chapter

    chunks = sliding_window_chunks(
        text, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap
    )
    total_passes = len(chunks) * len(PASS_ORDER) + 2  # PASS_ORDER passes per chunk + intra_dedup + canonicalization

    job_id = _job_store.create(total_passes=total_passes)
    tracker = ProgressTracker(job_id=job_id, store=_job_store)

    def _run() -> None:
        try:
            result = analyze_chapter(
                novel_id=novel_id,
                chapter_number=chapter_number,
                raw_text=text,
                chapter_title=None,
                use_mock_llm=None,
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
                progress=tracker,
                replace=replace,
            )
            _job_store.mark_done(job_id, result)
        except Exception as exc:
            _job_store.mark_error(job_id, str(exc))

    _executor.submit(_run)
    return job_id


def submit_generation_job(*, novel_id: str, chapter_number: int, ingest: bool) -> str:
    job_id = _job_store.create(total_passes=0)  # label-only progress
    tracker = ProgressTracker(job_id=job_id, store=_job_store)

    def _run() -> None:
        try:
            from pipeline.generation.loop import generate_chapter

            result = generate_chapter(
                novel_id, chapter_number, ingest=ingest, progress=tracker
            )
            _job_store.mark_done(job_id, {
                "chapter_number": result.chapter_number,
                "iterations": result.iterations,
                "passed": result.report.passed,
                "fails": len(result.report.fails),
                "warns": len(result.report.warns),
                "ingested": result.ingested,
                "chapter_id": result.chapter_id,
                "text": result.text,
            })
        except Exception as exc:
            _job_store.mark_error(job_id, str(exc))

    _executor.submit(_run)
    return job_id


__all__ = [
    "JobRecord",
    "JobStore",
    "ProgressTracker",
    "get_job",
    "submit_generation_job",
    "submit_job",
]
