# Chapter Processing UI — Design

## Goal

Allow users to submit a new chapter for pipeline processing from the web app. Currently processing requires the CLI (`uv run novel-pipeline process-chapter`). This feature adds a "Process Chapter" form to the wiki web app so chapter text can be pasted and submitted from the browser, with live progress feedback via polling.

## Decisions

- **Input:** paste text into a `<textarea>` (no file upload)
- **Progress:** polling (`GET /api/jobs/{id}` every 2 seconds)
- **Scope:** read-write for chapter submission only; all other views remain read-only

## User Flow

```
1. User navigates to /novels/:id/process (via sidebar "Process Chapter" link)
2. Fills in chapter number + pastes chapter text
3. Submits form
4. UI immediately shows "Submitted — processing…" with the job ID
5. UI polls GET /api/jobs/{job_id} every 2 seconds
6. Progress bar / status line updates: "Running — entity_deltas (pass 4 of 7)"
7. On "done": success message + "View Characters" and "View Timeline" links
8. On "error": error message with the exception text
```

## Architecture

### Backend — new endpoints in `backend/api/`

#### `POST /api/novels/{novel_id}/chapters/process`

Request body:
```json
{"number": 4, "text": "Chapter IV. ..."}
```

Response (202 Accepted):
```json
{"job_id": "<uuid>"}
```

Validates:
- Novel exists (404 if not)
- Chapter number is a positive integer
- Text is non-empty

Starts the job in a `ThreadPoolExecutor` (1 worker is enough for a local tool) and returns immediately.

#### `GET /api/jobs/{job_id}`

Response:
```json
{
  "job_id": "<uuid>",
  "status": "pending" | "running" | "done" | "error",
  "current_pass": "entity_deltas",
  "passes_done": 3,
  "total_passes": 7,
  "result": {...},
  "error": null
}
```

- `current_pass`: name of the LLM pass currently running (null if not yet running or finished)
- `passes_done`: how many passes have completed across all chunks
- `total_passes`: `len(chunks) * 6 + 1` (6 extraction passes per chunk + 1 canonicalization); computed at job-start time
- `result`: the `process_chapter()` return dict on success, null otherwise
- `error`: exception message on failure, null otherwise

Returns 404 if the job ID is unknown.

### Job runner — `backend/api/jobs.py`

New module with:

```python
class JobStore:
    """Thread-safe in-memory job registry."""
    ...

class ProgressTracker:
    """Passed into the extraction loop; called after each LLM pass."""
    def on_pass_start(self, pass_name: str) -> None: ...
    def on_pass_done(self, pass_name: str) -> None: ...

def submit_job(novel_id: str, chapter_number: int, text: str) -> str:
    """Start a pipeline job; return job_id."""
    ...
```

`JobStore` is a module-level singleton (a plain `dict` protected by a `threading.Lock`).

`ProgressTracker` is threaded through the extractor via an optional `progress` parameter — no monkey-patching. Added to `ChapterExtractor.extract_chapter`, then passed into `extract_chunk`, then called inside `_run_llm_pass` before and after each LLM call.

### Pipeline changes — `backend/pipeline/extraction/extractor.py`

Add optional `progress` parameter:

```python
def extract_chapter(
    self,
    chunks: list[str],
    context: dict,
    progress: Any | None = None,
) -> dict:
    ...
    for pass_name in PASS_ORDER:
        if progress:
            progress.on_pass_start(pass_name)
        payload = self._run_llm_pass(pass_name, chunk, context)
        if progress:
            progress.on_pass_done(pass_name)
    ...
```

This is a backwards-compatible addition — existing callers pass no `progress` and behavior is unchanged.

### Frontend — `frontend/src/routes/Process.tsx`

Single new route component. Uses:
- `useMutation` (TanStack Query) to submit the form
- `useQuery` with `refetchInterval: 2000` to poll the job status while `status === "running" | "pending"`
- Stops polling when status is `"done"` or `"error"`

### Frontend — `frontend/src/App.tsx`

One new route: `/novels/:novelId/process`

### Frontend — `frontend/src/components/Sidebar.tsx`

Add "Process Chapter" link to the nav list (below "Continuity").

## New Files

| File | Purpose |
|--|--|
| `backend/api/jobs.py` | JobStore, ProgressTracker, submit_job, get_job |
| `backend/api/routes/process.py` | POST and GET endpoints |
| `frontend/src/routes/Process.tsx` | Form + polling UI |

## Modified Files

| File | Change |
|--|--|
| `backend/api/app.py` | mount process router |
| `backend/api/schemas.py` | `ProcessRequest`, `JobStatus` Pydantic models |
| `backend/pipeline/extraction/extractor.py` | add optional `progress` param to `extract_chapter` |
| `backend/pipeline/pipeline.py` | pass `progress` through to `ChapterExtractor.extract_chapter` |
| `frontend/src/App.tsx` | add `/novels/:novelId/process` route |
| `frontend/src/components/Sidebar.tsx` | add "Process Chapter" nav link |

## Error Handling

- Chapter number already exists → 409 Conflict (the pipeline itself raises `ValueError`; the job runner catches it and sets `status = "error"`)
- LLM rate limit mid-run → job status becomes `"error"` with the exception message; user can retry
- Invalid novel ID → 404 before job is even submitted

## Testing

- Unit test: `JobStore` thread safety (submit + get + concurrent access)
- Unit test: `ProgressTracker.on_pass_done` increments `passes_done` correctly
- Integration test (mock LLM): POST → 202, poll → done, verify chapter appears in `/api/novels/{id}/chapters`

## Out of Scope

- Novel creation from the UI
- Re-processing an existing chapter
- Job history / persistence (jobs are lost on server restart — fine for local tool)
- Multiple concurrent novel processing jobs
- File upload input
- Cancel in-flight job
