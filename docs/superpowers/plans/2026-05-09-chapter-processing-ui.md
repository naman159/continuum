# Chapter Processing UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Process Chapter" form to the web app so users can paste chapter text and submit it through the extraction pipeline, with live polling progress feedback.

**Architecture:** A new `POST /api/novels/{id}/chapters/process` endpoint submits a background job to a `ThreadPoolExecutor` and returns a `job_id` immediately. The frontend polls `GET /api/jobs/{job_id}` every 2 seconds. Progress hooks in `ChapterExtractor` update the job record after each LLM pass. A new `Process.tsx` React page handles the form submission and polling loop.

**Tech Stack:** FastAPI `BackgroundTasks`-free (direct `ThreadPoolExecutor`), `threading.Lock` for job-store safety, TanStack Query `refetchInterval` for polling, React `useState`/`useEffect`.

---

## File Map

| File | Action | Purpose |
|--|--|--|
| `backend/pipeline/extraction/extractor.py` | Modify | Add optional `progress` param to `extract_chapter` + `extract_chunk`; call hooks around `_run_llm_pass` |
| `backend/pipeline/pipeline.py` | Modify | Thread `progress` through `process_chapter`; wrap canonicalization step |
| `backend/api/jobs.py` | Create | `JobStore`, `ProgressTracker`, `submit_job`, `get_job` |
| `backend/api/schemas.py` | Modify | Add `ProcessRequest`, `JobStatusResponse` |
| `backend/api/routes/process.py` | Create | `POST /api/novels/{id}/chapters/process`, `GET /api/jobs/{id}` |
| `backend/api/app.py` | Modify | Mount process router |
| `backend/tests/api/test_process.py` | Create | API-level tests for job submission + polling |
| `frontend/src/routes/Process.tsx` | Create | Form + polling UI |
| `frontend/src/App.tsx` | Modify | Add `/novels/:novelId/process` route |
| `frontend/src/components/Sidebar.tsx` | Modify | Add "Process Chapter" nav link |

---

## Task 1: Add progress hooks to `ChapterExtractor`

**Files:**
- Modify: `backend/pipeline/extraction/extractor.py`
- Test: `backend/tests/test_extractor_progress.py` (new)

The `progress` object has two methods: `on_pass_start(pass_name: str)` and `on_pass_done(pass_name: str)`. They are called around each real LLM pass inside `extract_chunk`. In mock mode the hooks are skipped (no LLM passes run).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_extractor_progress.py`:

```python
from __future__ import annotations

from types import SimpleNamespace


class FakeProgress:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def on_pass_start(self, name: str) -> None:
        self.calls.append(("start", name))

    def on_pass_done(self, name: str) -> None:
        self.calls.append(("done", name))


def _make_fake_completion(responses: dict[str, str]):
    """Returns a fake litellm completion function."""
    call_count = [0]

    def fake(**_kwargs):
        # Return empty JSON for every pass
        content = "{}"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return fake


def test_extract_chunk_calls_progress_hooks_for_each_pass(monkeypatch):
    from pipeline.extraction import extractor as ext_module
    from pipeline.extraction.prompts import PASS_ORDER

    fake_completion = _make_fake_completion({})
    monkeypatch.setattr(ext_module, "_load_completion", lambda: fake_completion)

    from pipeline.extraction.extractor import ChapterExtractor

    progress = FakeProgress()
    extractor = ChapterExtractor(use_mock=False)
    extractor.extract_chunk("some text", {}, progress=progress)

    starts = [name for action, name in progress.calls if action == "start"]
    dones = [name for action, name in progress.calls if action == "done"]
    assert starts == list(PASS_ORDER)
    assert dones == list(PASS_ORDER)


def test_extract_chunk_skips_progress_in_mock_mode():
    from pipeline.extraction.extractor import ChapterExtractor

    progress = FakeProgress()
    extractor = ChapterExtractor(use_mock=True)
    extractor.extract_chunk("some text", {}, progress=progress)

    assert progress.calls == []


def test_extract_chapter_accumulates_progress_across_chunks(monkeypatch):
    from pipeline.extraction import extractor as ext_module
    from pipeline.extraction.prompts import PASS_ORDER

    monkeypatch.setattr(ext_module, "_load_completion", lambda: _make_fake_completion({}))

    from pipeline.extraction.extractor import ChapterExtractor

    progress = FakeProgress()
    extractor = ChapterExtractor(use_mock=False)
    extractor.extract_chapter(["chunk1", "chunk2"], {}, progress=progress)

    dones = [name for action, name in progress.calls if action == "done"]
    assert len(dones) == len(PASS_ORDER) * 2  # 6 passes × 2 chunks = 12
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/test_extractor_progress.py -v
```

Expected: `AttributeError` or `TypeError` — `extract_chunk` doesn't accept `progress` yet.

- [ ] **Step 3: Implement the changes in `extractor.py`**

In `backend/pipeline/extraction/extractor.py`, make these three changes:

**Change 1** — `extract_chapter` signature and body:
```python
def extract_chapter(
    self, chunks: list[str], context: dict[str, Any], progress: Any | None = None
) -> dict[str, Any]:
    if not chunks:
        return empty_extraction()

    results = [self.extract_chunk(chunk, context, progress=progress) for chunk in chunks]
    return merge_extractions(results)
```

**Change 2** — `extract_chunk` signature and body:
```python
def extract_chunk(
    self, chunk: str, context: dict[str, Any], progress: Any | None = None
) -> dict[str, Any]:
    if self.use_mock:
        return self._mock_extract(chunk, context)

    pass_payload: dict[str, Any] = {}
    for pass_name in PASS_ORDER:
        if progress is not None:
            progress.on_pass_start(pass_name)
        payload = self._run_llm_pass(pass_name, chunk, context)
        if progress is not None:
            progress.on_pass_done(pass_name)
        pass_payload[pass_name] = payload

    normalized = self._compose_from_pass_payload(pass_payload)
    return _normalize_extraction(normalized)
```

No other changes to `extractor.py`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/test_extractor_progress.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Confirm existing tests still pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest -q
```

Expected: `23 passed` (existing suite unchanged).

- [ ] **Step 6: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add backend/pipeline/extraction/extractor.py backend/tests/test_extractor_progress.py
git commit -m "feat(pipeline): add optional progress hooks to ChapterExtractor"
```

---

## Task 2: Thread `progress` through `process_chapter`

**Files:**
- Modify: `backend/pipeline/pipeline.py`

Add `progress` as an optional keyword-only argument to `process_chapter`. Pass it to `extractor.extract_chapter`. Wrap the canonicalization step with `on_pass_start("canonicalization")` / `on_pass_done("canonicalization")`.

- [ ] **Step 1: Modify `process_chapter` in `backend/pipeline/pipeline.py`**

Find the function at line 146. Add `progress: Any | None = None` to the signature and wire it through. Add `from typing import Any` if not already imported.

Replace:
```python
def process_chapter(
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    chapter_title: str | None,
    use_mock_llm: bool | None,
    chunk_size: int,
    chunk_overlap: int,
) -> dict[str, Any]:
```

With:
```python
def process_chapter(
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    chapter_title: str | None,
    use_mock_llm: bool | None,
    chunk_size: int,
    chunk_overlap: int,
    progress: Any | None = None,
) -> dict[str, Any]:
```

Then inside the function body, replace:
```python
        extractor = ChapterExtractor(use_mock=use_mock_llm)
        extracted = extractor.extract_chapter(chunks=chunks, context=context)

        canonicalizer = CharacterCanonicalizer(db, novel_id=novel_id, use_mock=use_mock_llm)
        canonicalizer.canonicalize(
            chapter_text=raw_text,
            candidate_names=collect_character_names(extracted),
        )
```

With:
```python
        extractor = ChapterExtractor(use_mock=use_mock_llm)
        extracted = extractor.extract_chapter(chunks=chunks, context=context, progress=progress)

        if progress is not None:
            progress.on_pass_start("canonicalization")
        canonicalizer = CharacterCanonicalizer(db, novel_id=novel_id, use_mock=use_mock_llm)
        canonicalizer.canonicalize(
            chapter_text=raw_text,
            candidate_names=collect_character_names(extracted),
        )
        if progress is not None:
            progress.on_pass_done("canonicalization")
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest -q
```

Expected: `26 passed` (23 existing + 3 new extractor progress tests).

- [ ] **Step 3: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add backend/pipeline/pipeline.py
git commit -m "feat(pipeline): thread progress tracker through process_chapter"
```

---

## Task 3: Job store and tracker

**Files:**
- Create: `backend/api/jobs.py`
- Test: `backend/tests/api/test_jobs.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_jobs.py`:

```python
from __future__ import annotations

import threading


def test_job_store_create_and_get():
    from api.jobs import JobStore

    store = JobStore()
    job_id = store.create(total_passes=7)
    record = store.get(job_id)

    assert record is not None
    assert record.job_id == job_id
    assert record.status == "pending"
    assert record.total_passes == 7
    assert record.passes_done == 0
    assert record.current_pass is None


def test_job_store_get_missing_returns_none():
    from api.jobs import JobStore

    store = JobStore()
    assert store.get("nonexistent") is None


def test_progress_tracker_on_pass_start():
    from api.jobs import JobStore, ProgressTracker

    store = JobStore()
    job_id = store.create(total_passes=7)
    tracker = ProgressTracker(job_id=job_id, store=store)

    tracker.on_pass_start("chapter_summary")

    record = store.get(job_id)
    assert record.status == "running"
    assert record.current_pass == "chapter_summary"


def test_progress_tracker_on_pass_done():
    from api.jobs import JobStore, ProgressTracker

    store = JobStore()
    job_id = store.create(total_passes=7)
    tracker = ProgressTracker(job_id=job_id, store=store)

    tracker.on_pass_start("chapter_summary")
    tracker.on_pass_done("chapter_summary")
    tracker.on_pass_start("new_entities")
    tracker.on_pass_done("new_entities")

    record = store.get(job_id)
    assert record.passes_done == 2
    assert record.current_pass is None


def test_job_store_thread_safety():
    from api.jobs import JobStore, ProgressTracker

    store = JobStore()
    job_id = store.create(total_passes=100)
    tracker = ProgressTracker(job_id=job_id, store=store)

    errors: list[Exception] = []

    def increment():
        try:
            for _ in range(10):
                tracker.on_pass_start("x")
                tracker.on_pass_done("x")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=increment) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    record = store.get(job_id)
    assert record.passes_done == 100  # 10 threads × 10 increments
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/api/test_jobs.py -v
```

Expected: `ModuleNotFoundError: No module named 'api.jobs'`.

- [ ] **Step 3: Create `backend/api/jobs.py`**

```python
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def create(self, *, total_passes: int) -> str:
        job_id = str(uuid.uuid4())
        record = JobRecord(job_id=job_id, total_passes=total_passes)
        with self._lock:
            self._jobs[job_id] = record
        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

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


# Module-level singletons — one store and one executor for the process lifetime.
_job_store = JobStore()
_executor = ThreadPoolExecutor(max_workers=2)


def get_job(job_id: str) -> JobRecord | None:
    return _job_store.get(job_id)


def submit_job(*, novel_id: str, chapter_number: int, text: str) -> str:
    from pipeline.config import settings
    from pipeline.extraction.chunker import sliding_window_chunks
    from pipeline.pipeline import process_chapter

    chunks = sliding_window_chunks(
        text, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap
    )
    # 6 extraction passes per chunk + 1 canonicalization pass
    total_passes = len(chunks) * 6 + 1

    job_id = _job_store.create(total_passes=total_passes)
    tracker = ProgressTracker(job_id=job_id, store=_job_store)

    def _run() -> None:
        try:
            result = process_chapter(
                novel_id=novel_id,
                chapter_number=chapter_number,
                raw_text=text,
                chapter_title=None,
                use_mock_llm=None,
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
                progress=tracker,
            )
            _job_store.mark_done(job_id, result)
        except Exception as exc:
            _job_store.mark_error(job_id, str(exc))

    _executor.submit(_run)
    return job_id


__all__ = ["JobRecord", "JobStore", "ProgressTracker", "get_job", "submit_job"]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/api/test_jobs.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Full suite check**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest -q
```

Expected: `31 passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add backend/api/jobs.py backend/tests/api/test_jobs.py
git commit -m "feat(api): job store, progress tracker, submit_job"
```

---

## Task 4: Schemas + API endpoints

**Files:**
- Modify: `backend/api/schemas.py`
- Create: `backend/api/routes/process.py`
- Modify: `backend/api/app.py`
- Create: `backend/tests/api/test_process.py`

- [ ] **Step 1: Append schemas to `backend/api/schemas.py`**

```python
class ProcessRequest(BaseModel):
    number: int
    text: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    current_pass: str | None = None
    passes_done: int = 0
    total_passes: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
```

- [ ] **Step 2: Write failing tests**

Create `backend/tests/api/test_process.py`:

```python
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import app
from api.jobs import JobRecord


client = TestClient(app)


def _make_novel_id():
    return str(uuid4())


def test_submit_job_returns_202_with_job_id():
    novel_id = _make_novel_id()

    with patch("api.routes.process.queries.get_novel", return_value={"id": novel_id, "title": "T", "max_chapter": 1, "author": None, "language": None, "created_at": "2026-01-01"}), \
         patch("api.routes.process.submit_job", return_value="test-job-123") as mock_submit:
        response = client.post(
            f"/api/novels/{novel_id}/chapters/process",
            json={"number": 2, "text": "Chapter text here."},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == "test-job-123"
    mock_submit.assert_called_once_with(
        novel_id=novel_id,
        chapter_number=2,
        text="Chapter text here.",
    )


def test_submit_job_404_when_novel_not_found():
    novel_id = _make_novel_id()

    with patch("api.routes.process.queries.get_novel", return_value=None):
        response = client.post(
            f"/api/novels/{novel_id}/chapters/process",
            json={"number": 2, "text": "Some text."},
        )

    assert response.status_code == 404


def test_submit_job_422_when_text_empty():
    novel_id = _make_novel_id()

    with patch("api.routes.process.queries.get_novel", return_value={"id": novel_id, "title": "T", "max_chapter": 1, "author": None, "language": None, "created_at": "2026-01-01"}):
        response = client.post(
            f"/api/novels/{novel_id}/chapters/process",
            json={"number": 2, "text": "   "},
        )

    assert response.status_code == 422


def test_get_job_returns_status():
    record = JobRecord(
        job_id="abc",
        status="running",
        current_pass="events",
        passes_done=4,
        total_passes=7,
    )

    with patch("api.routes.process.get_job", return_value=record):
        response = client.get("/api/jobs/abc")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["current_pass"] == "events"
    assert body["passes_done"] == 4
    assert body["total_passes"] == 7


def test_get_job_404_for_unknown_id():
    with patch("api.routes.process.get_job", return_value=None):
        response = client.get("/api/jobs/nonexistent")

    assert response.status_code == 404
```

- [ ] **Step 3: Run to verify they fail**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/api/test_process.py -v
```

Expected: FAIL — routes don't exist yet.

- [ ] **Step 4: Create `backend/api/routes/process.py`**

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from api import queries
from api.jobs import get_job, submit_job
from api.schemas import JobStatusResponse, ProcessRequest

router = APIRouter(tags=["process"])


@router.post(
    "/api/novels/{novel_id}/chapters/process",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=dict,
)
def process_chapter(novel_id: UUID, body: ProcessRequest) -> dict:
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty")

    novel = queries.get_novel(novel_id)
    if novel is None:
        raise HTTPException(status_code=404, detail="Novel not found")

    job_id = submit_job(
        novel_id=str(novel_id),
        chapter_number=body.number,
        text=body.text,
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
```

- [ ] **Step 5: Mount the router in `backend/api/app.py`**

Add the import and `include_router` call. The existing import line is:
```python
from api.routes import characters, chapters, continuity, novels, relationships, threads, timeline
```

Replace with:
```python
from api.routes import characters, chapters, continuity, novels, process, relationships, threads, timeline
```

Add after the existing `app.include_router` calls:
```python
app.include_router(process.router)
```

- [ ] **Step 6: Run tests**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/api/test_process.py -v
```

Expected: `5 passed`.

- [ ] **Step 7: Full suite check**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest -q
```

Expected: `36 passed`.

- [ ] **Step 8: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add backend/api/schemas.py backend/api/routes/process.py backend/api/app.py backend/tests/api/test_process.py
git commit -m "feat(api): chapter processing endpoints POST + GET /jobs/{id}"
```

---

## Task 5: Frontend — Process page + routing

**Files:**
- Create: `frontend/src/routes/Process.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Create `frontend/src/routes/Process.tsx`**

```typescript
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

type JobStatus = {
  job_id: string;
  status: "pending" | "running" | "done" | "error";
  current_pass: string | null;
  passes_done: number;
  total_passes: number;
  result: Record<string, unknown> | null;
  error: string | null;
};

async function postChapter(novelId: string, number: number, text: string): Promise<{ job_id: string }> {
  const res = await fetch(`/api/novels/${novelId}/chapters/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number, text }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function fetchJob(jobId: string): Promise<JobStatus> {
  const res = await fetch(`/api/jobs/${jobId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export default function Process() {
  const { novelId } = useParams();
  const [chapterNumber, setChapterNumber] = useState<number>(1);
  const [text, setText] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: ({ number, text }: { number: number; text: string }) =>
      postChapter(novelId!, number, text),
    onSuccess: (data) => setJobId(data.job_id),
  });

  const jobQuery = useQuery<JobStatus>({
    queryKey: ["job", jobId],
    queryFn: () => fetchJob(jobId!),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "done" || status === "error" ? false : 2000;
    },
  });

  const job = jobQuery.data;
  const isRunning = Boolean(jobId) && job?.status !== "done" && job?.status !== "error";

  return (
    <div>
      <h1>Process Chapter</h1>

      {!jobId && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            mutation.mutate({ number: chapterNumber, text });
          }}
        >
          <div style={{ marginBottom: 12 }}>
            <label htmlFor="chap-number">Chapter number</label>
            <br />
            <input
              id="chap-number"
              type="number"
              min={1}
              value={chapterNumber}
              onChange={(e) => setChapterNumber(Number(e.target.value))}
              style={{ width: 80, marginTop: 4 }}
            />
          </div>
          <div style={{ marginBottom: 12 }}>
            <label htmlFor="chap-text">Chapter text</label>
            <br />
            <textarea
              id="chap-text"
              rows={20}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Paste chapter text here…"
              style={{ width: "100%", marginTop: 4, fontFamily: "monospace", fontSize: 13 }}
            />
          </div>
          {mutation.isError && (
            <p style={{ color: "red" }}>Error: {(mutation.error as Error).message}</p>
          )}
          <button type="submit" disabled={mutation.isPending || !text.trim()}>
            {mutation.isPending ? "Submitting…" : "Process chapter"}
          </button>
        </form>
      )}

      {jobId && (
        <div>
          <p className="muted">Job ID: <code>{jobId}</code></p>

          {isRunning && (
            <div>
              <p>⏳ Processing…</p>
              {job && (
                <p>
                  {job.current_pass
                    ? `Running pass: ${job.current_pass}`
                    : "Starting…"}
                  {job.total_passes > 0 && ` (${job.passes_done} / ${job.total_passes})`}
                </p>
              )}
              <progress
                value={job?.passes_done ?? 0}
                max={job?.total_passes ?? 1}
                style={{ width: "100%" }}
              />
            </div>
          )}

          {job?.status === "done" && (
            <div>
              <p style={{ color: "green" }}>✓ Done!</p>
              {job.result && (
                <ul>
                  <li>{job.result.new_characters as number} new characters</li>
                  <li>{job.result.events as number} events</li>
                  <li>{job.result.thread_updates as number} thread updates</li>
                  <li>{job.result.continuity_flags as number} continuity flags</li>
                </ul>
              )}
              <p>
                <Link to={`/novels/${novelId}/characters`}>View Characters</Link>
                {" · "}
                <Link to={`/novels/${novelId}/timeline`}>View Timeline</Link>
              </p>
              <button onClick={() => { setJobId(null); setText(""); }}>
                Process another chapter
              </button>
            </div>
          )}

          {job?.status === "error" && (
            <div>
              <p style={{ color: "red" }}>✗ Error</p>
              <pre style={{ background: "#fee", padding: 8, borderRadius: 4, whiteSpace: "pre-wrap" }}>
                {job.error}
              </pre>
              <button onClick={() => { setJobId(null); }}>
                Try again
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add route to `frontend/src/App.tsx`**

Add the import alongside the others:
```typescript
import Process from "./routes/Process";
```

Add the route inside `<Routes>`:
```typescript
<Route path="/novels/:novelId/process" element={<Layout><Process /></Layout>} />
```

- [ ] **Step 3: Add nav link to `frontend/src/components/Sidebar.tsx`**

In the `links` array inside `Sidebar`, append the "Process Chapter" entry after the "Continuity" entry:

```typescript
const links = novelId
  ? [
      ["Characters", `/novels/${novelId}/characters`],
      ["Chapters", `/novels/${novelId}/chapters`],
      ["Timeline", `/novels/${novelId}/timeline`],
      ["Threads", `/novels/${novelId}/threads`],
      ["Relationships", `/novels/${novelId}/relationships`],
      ["Continuity", `/novels/${novelId}/continuity`],
      ["Process Chapter", `/novels/${novelId}/process`],
    ]
  : [];
```

- [ ] **Step 4: TypeScript check**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/frontend
npx tsc --noEmit
```

Expected: 0 errors. If there are type errors on `job.result.new_characters as number`, use `(job.result as Record<string, number>).new_characters` instead.

- [ ] **Step 5: Python tests still pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest -q
```

Expected: `36 passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git add frontend/src/routes/Process.tsx frontend/src/App.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat(frontend): chapter processing form with polling progress"
```

---

## Task 6: End-to-end smoke test

**No new files — manual verification.**

- [ ] **Step 1: Restart the API with the new code**

```bash
# Kill existing API if running
kill $(cat /tmp/api.pid) 2>/dev/null

# Start fresh from backend/
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run novel-webapp --port 8000 > /tmp/api.log 2>&1 &
echo $! > /tmp/api.pid
sleep 2
curl -s http://127.0.0.1:8000/api/health
```

Expected: `{"status":"ok"}`.

- [ ] **Step 2: Test the POST endpoint directly**

```bash
NOVEL_ID=$(curl -s http://127.0.0.1:8000/api/novels | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['id'])")
echo "Novel: $NOVEL_ID"

JOB_RESPONSE=$(curl -s -X POST "http://127.0.0.1:8000/api/novels/$NOVEL_ID/chapters/process" \
  -H "Content-Type: application/json" \
  -d '{"number": 99, "text": "The quick brown fox jumps over the lazy dog. Elizabeth watched with amusement."}')
echo "Job response: $JOB_RESPONSE"

JOB_ID=$(echo $JOB_RESPONSE | python3 -c "import json,sys;print(json.load(sys.stdin)['job_id'])")
echo "Job ID: $JOB_ID"
sleep 2
curl -s "http://127.0.0.1:8000/api/jobs/$JOB_ID" | python3 -m json.tool
```

Expected: job status response with `status` in `pending|running|done|error` and a `job_id`.

- [ ] **Step 3: Verify frontend loads the Process page**

```bash
# Vite should already be running at :5173
# If not: cd /Users/naman/Desktop/gitprojs/continuum/frontend && npm run dev &

curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5173/novels/de68d0c2-5d1d-445a-944d-14b6010d0af0/process
```

Expected: `200`.

- [ ] **Step 4: Commit the smoke-test confirmation (no code changes)**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
git log --oneline -5
```

Expected: the 5 commits from this feature visible, branch still `wiki-webapp`. No stray uncommitted changes.

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|--|--|
| Paste text input | Task 5 — `<textarea>` in `Process.tsx` |
| Chapter number input | Task 5 — `<input type="number">` |
| `POST /api/novels/{id}/chapters/process` | Task 4 |
| `GET /api/jobs/{id}` | Task 4 |
| In-memory job store with thread safety | Task 3 |
| `ProgressTracker` calling `on_pass_start`/`on_pass_done` | Task 3 |
| Progress hooks in `ChapterExtractor` | Task 1 |
| Progress threaded through `process_chapter` | Task 2 |
| Polling every 2 seconds | Task 5 — `refetchInterval: 2000` |
| Stop polling on done/error | Task 5 — `refetchInterval` returns `false` |
| Progress bar with pass name | Task 5 |
| Success: show result summary + links | Task 5 |
| Error: show message + retry | Task 5 |
| "Process Chapter" in sidebar | Task 5 |
| Novel 404 before submitting | Task 4 |
| Empty text → 422 | Task 4 |
| Jobs lost on restart (intentional) | Task 3 — in-memory only |

**Type consistency check:** `JobRecord` defined in Task 3; `JobStatusResponse` in Task 4 reads from it; `JobStatus` TypeScript type in Task 5 matches the JSON shape. All field names consistent: `job_id`, `status`, `current_pass`, `passes_done`, `total_passes`, `result`, `error`.
