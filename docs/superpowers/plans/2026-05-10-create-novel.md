# Create Novel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to create a new novel from the Novels list page and navigate to Process Chapter on success.

**Architecture:** Add a `POST /api/novels` backend endpoint + `NovelCreate` schema; add a `create_novel()` query that inserts a row into the `novels` table. In the frontend, add an inline toggle form to `Novels.tsx` using `useMutation` that POSTs and then navigates to `/novels/:id/process`.

**Tech Stack:** FastAPI, Pydantic, psycopg (backend); React, React Query (`useMutation`/`useQueryClient`), React Router (`useNavigate`), TypeScript (frontend).

---

### Task 1: Add `NovelCreate` schema

**Files:**
- Modify: `backend/api/schemas.py`

- [ ] **Step 1: Add `NovelCreate` to schemas**

Open `backend/api/schemas.py` and add this class after the `NovelSummary` class:

```python
class NovelCreate(BaseModel):
    title: str
    author: str | None = None
    language: str | None = None
```

- [ ] **Step 2: Commit**

```bash
git add backend/api/schemas.py
git commit -m "feat: add NovelCreate request schema"
```

---

### Task 2: Write failing tests for POST /api/novels

**Files:**
- Modify: `backend/tests/api/test_novels.py`

- [ ] **Step 1: Add tests**

Append to `backend/tests/api/test_novels.py`:

```python
def test_create_novel(fake_db_factory, client):
    fake_db_factory()
    response = client.post("/api/novels", json={"title": "New Novel", "author": "Me", "language": "en"})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "New Novel"
    assert body["author"] == "Me"
    assert body["language"] == "en"
    assert body["max_chapter"] == 0
    assert "id" in body
    assert "created_at" in body


def test_create_novel_optional_fields_omitted(fake_db_factory, client):
    fake_db_factory()
    response = client.post("/api/novels", json={"title": "Minimal"})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Minimal"
    assert body["author"] is None
    assert body["language"] is None


def test_create_novel_blank_title(fake_db_factory, client):
    fake_db_factory()
    response = client.post("/api/novels", json={"title": "   "})
    assert response.status_code == 422


def test_create_novel_missing_title(fake_db_factory, client):
    fake_db_factory()
    response = client.post("/api/novels", json={})
    assert response.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/api/test_novels.py::test_create_novel tests/api/test_novels.py::test_create_novel_optional_fields_omitted tests/api/test_novels.py::test_create_novel_blank_title tests/api/test_novels.py::test_create_novel_missing_title -v
```

Expected: 4 FAILs — `404 Not Found` or similar (endpoint doesn't exist yet).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/api/test_novels.py
git commit -m "test: add failing tests for POST /api/novels"
```

---

### Task 3: Implement `create_novel` query

**Files:**
- Modify: `backend/api/queries.py`

- [ ] **Step 1: Add `create_novel` to queries**

Open `backend/api/queries.py`. Add the following imports at the top if not already present:

```python
from datetime import datetime, timezone
from uuid import uuid4
```

Then add this function (place it after `get_novel`):

```python
def create_novel(title: str, author: str | None, language: str | None) -> dict[str, Any]:
    db = _get_db()
    if hasattr(db, "novels"):
        novel: dict[str, Any] = {
            "id": uuid4(),
            "title": title,
            "author": author,
            "language": language,
            "created_at": datetime.now(timezone.utc),
        }
        db.novels.append(novel)
        return {**novel, "max_chapter": 0}
    return _create_novel_real(db, title, author, language)


def _create_novel_real(db: DBClient, title: str, author: str | None, language: str | None) -> dict[str, Any]:
    row = db.fetchone(
        """
        INSERT INTO novels (id, title, author, language, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        RETURNING id, title, author, language, created_at
        """,
        (str(uuid4()), title, author, language),
        dict_rows=True,
        commit=True,
    )
    return {**dict(row), "max_chapter": 0}
```

- [ ] **Step 2: Commit**

```bash
git add backend/api/queries.py
git commit -m "feat: add create_novel query"
```

---

### Task 4: Add POST /api/novels endpoint

**Files:**
- Modify: `backend/api/routes/novels.py`

- [ ] **Step 1: Add import and endpoint**

Open `backend/api/routes/novels.py`. Add `NovelCreate` to the schemas import and add the new route:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from api import queries
from api.schemas import NovelCreate, NovelSummary

router = APIRouter(prefix="/api/novels", tags=["novels"])


@router.get("", response_model=list[NovelSummary])
def list_novels() -> list[NovelSummary]:
    return [NovelSummary(**row) for row in queries.list_novels()]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=NovelSummary)
def create_novel(body: NovelCreate) -> NovelSummary:
    if not body.title or not body.title.strip():
        raise HTTPException(status_code=422, detail="title must not be blank")
    row = queries.create_novel(body.title.strip(), body.author, body.language)
    return NovelSummary(**row)


@router.get("/{novel_id}", response_model=NovelSummary)
def get_novel(novel_id: UUID) -> NovelSummary:
    row = queries.get_novel(novel_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Novel not found")
    return NovelSummary(**row)
```

- [ ] **Step 2: Run the failing tests — they should now pass**

```bash
cd backend && python -m pytest tests/api/test_novels.py -v
```

Expected: all 8 tests PASS (4 existing + 4 new).

- [ ] **Step 3: Commit**

```bash
git add backend/api/routes/novels.py
git commit -m "feat: add POST /api/novels endpoint"
```

---

### Task 5: Update Novels.tsx with inline create form

**Files:**
- Modify: `frontend/src/routes/Novels.tsx`

- [ ] **Step 1: Replace the entire file**

Replace `frontend/src/routes/Novels.tsx` with:

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Novel } from "../api";

async function createNovel(title: string, author: string, language: string): Promise<Novel> {
  const res = await fetch("/api/novels", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      author: author.trim() || null,
      language: language.trim() || null,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export default function Novels() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [language, setLanguage] = useState("");

  const { data, isLoading, error } = useQuery({ queryKey: ["novels"], queryFn: api.novels });

  const mutation = useMutation({
    mutationFn: () => createNovel(title, author, language),
    onSuccess: (novel) => {
      queryClient.invalidateQueries({ queryKey: ["novels"] });
      navigate(`/novels/${novel.id}/process`);
    },
  });

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;

  return (
    <div>
      <h1>Novels</h1>

      {!showForm && (
        <button onClick={() => setShowForm(true)}>New Novel</button>
      )}

      {showForm && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!title.trim()) return;
            mutation.mutate();
          }}
          style={{ marginBottom: 16 }}
        >
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-title">Title</label>
            <br />
            <input
              id="novel-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              style={{ marginTop: 4 }}
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-author">Author (optional)</label>
            <br />
            <input
              id="novel-author"
              type="text"
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              style={{ marginTop: 4 }}
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-language">Language (optional)</label>
            <br />
            <input
              id="novel-language"
              type="text"
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              style={{ marginTop: 4 }}
            />
          </div>
          {mutation.isError && (
            <p style={{ color: "red" }}>Error: {(mutation.error as Error).message}</p>
          )}
          <button type="submit" disabled={mutation.isPending || !title.trim()}>
            {mutation.isPending ? "Creating…" : "Create Novel"}
          </button>
          {" "}
          <button type="button" onClick={() => setShowForm(false)} disabled={mutation.isPending}>
            Cancel
          </button>
        </form>
      )}

      {(!data || data.length === 0) && !showForm && <p>No novels.</p>}
      {data && data.length > 0 && (
        <ul>
          {data.map((n) => (
            <li key={n.id}>
              <Link to={`/novels/${n.id}/characters`}>{n.title}</Link>
              {" — "}
              {n.author ?? "Unknown"} · {n.max_chapter} chapter{n.max_chapter === 1 ? "" : "s"}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add `createNovel` return type to `api.ts`**

The `Novel` type is already exported from `frontend/src/api.ts` — no changes needed.

- [ ] **Step 3: Verify the app compiles**

```bash
cd frontend && npm run build
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/Novels.tsx
git commit -m "feat: add inline create novel form to Novels page"
```

---

### Task 6: Manual smoke test

- [ ] **Step 1: Start dev servers**

Backend:
```bash
cd backend && uvicorn api.app:app --reload --port 8000
```

Frontend (separate terminal):
```bash
cd frontend && npm run dev
```

- [ ] **Step 2: Verify the golden path**

1. Navigate to `http://localhost:5173/novels`
2. Click "New Novel" — form should expand inline
3. Fill in a title (required), optionally author and language
4. Click "Create Novel"
5. Should navigate to `/novels/<new-id>/process`
6. Navigate back to `/novels` — new novel should appear in the list

- [ ] **Step 3: Verify edge cases**

- Submit with blank title: button is disabled, form does not submit
- Click Cancel: form hides, list is unchanged
- Submit with only title filled in: succeeds, author/language shown as "Unknown"/omitted
