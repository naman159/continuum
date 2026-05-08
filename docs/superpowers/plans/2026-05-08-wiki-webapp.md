# Wiki Web App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local read-only wiki web app over the existing pipeline data, with character/timeline/threads/continuity views and an interactive relationship graph, gated by a global "as of chapter N" cap.

**Architecture:** Restructure repo into `backend/pipeline/` (existing code) + `backend/api/` (FastAPI JSON service) + `frontend/` (Vite + React + TypeScript). Single Python process serves API in dev (Vite proxies in dev; FastAPI serves built static frontend in prod).

**Tech Stack:** FastAPI, uvicorn, Pydantic (Python). React 18, TypeScript, Vite, React Router, TanStack Query, vis-network (Frontend). pytest + FastAPI TestClient (Python tests). Vitest + React Testing Library (frontend tests).

**Reference spec:** `docs/superpowers/specs/2026-05-08-wiki-webapp-design.md`

---

## Pre-flight

Run from project root: `/Users/naman/Desktop/gitprojs/continuum`. Recommended: do this on a feature branch (`git checkout -b wiki-webapp`) so the refactor and webapp can be reviewed/reverted together.

Sanity-check the existing test suite passes before any changes:

```bash
uv run pytest -q
# Expected: 8 passed
```

---

## Task 1: Move pipeline modules into `backend/pipeline/`

**Files:**
- Move: `config.py`, `pipeline.py`, `embeddings.py`, `main.py` → `backend/pipeline/`
- Move: `db/`, `extraction/`, `ingestion/`, `wiki/` → `backend/pipeline/`
- Create: `backend/__init__.py` (empty), `backend/pipeline/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Move files with git**

```bash
mkdir -p backend/pipeline
git mv config.py backend/pipeline/config.py
git mv pipeline.py backend/pipeline/pipeline.py
git mv embeddings.py backend/pipeline/embeddings.py
git mv main.py backend/pipeline/main.py
git mv db backend/pipeline/db
git mv extraction backend/pipeline/extraction
git mv ingestion backend/pipeline/ingestion
git mv wiki backend/pipeline/wiki
touch backend/__init__.py
```

- [ ] **Step 2: Create `backend/pipeline/__init__.py` re-exporting public API**

Create `backend/pipeline/__init__.py`:

```python
from .config import settings, LLM_CONFIG
from .pipeline import (
    init_db,
    create_novel,
    list_novels,
    process_chapter,
    load_story_context,
    main,
)

__all__ = [
    "settings",
    "LLM_CONFIG",
    "init_db",
    "create_novel",
    "list_novels",
    "process_chapter",
    "load_story_context",
    "main",
]
```

- [ ] **Step 3: Update `pyproject.toml`**

Replace the existing `[tool.setuptools]` and `[tool.setuptools.packages.find]` sections, and update `[project.scripts]`:

```toml
[project.scripts]
novel-pipeline = "pipeline.pipeline:main"
novel-wiki-character = "pipeline.wiki.character:main"
novel-wiki-timeline = "pipeline.wiki.timeline:main"
novel-wiki-threads = "pipeline.wiki.threads:main"
novel-wiki-relationships = "pipeline.wiki.relationships:main"

[tool.setuptools]
package-dir = {"" = "backend"}

[tool.setuptools.packages.find]
where = ["backend"]
```

Remove the existing `py-modules = ["config", "embeddings", "main", "pipeline"]` line.

- [ ] **Step 4: Update internal imports inside the package**

Run a search-and-replace on `backend/pipeline/**/*.py`. The five distinct old prefixes that must change:

| Old | New |
|--|--|
| `from config import` | `from pipeline.config import` |
| `from db.` | `from pipeline.db.` |
| `from embeddings import` | `from pipeline.embeddings import` |
| `from extraction.` | `from pipeline.extraction.` |
| `from ingestion.` | `from pipeline.ingestion.` |

Use this command:

```bash
cd backend/pipeline
find . -name "*.py" -type f -exec sed -i '' \
  -e 's/^from config import/from pipeline.config import/g' \
  -e 's/^from db\./from pipeline.db./g' \
  -e 's/^from embeddings import/from pipeline.embeddings import/g' \
  -e 's/^from extraction\./from pipeline.extraction./g' \
  -e 's/^from ingestion\./from pipeline.ingestion./g' \
  {} +
cd ../..
```

Verify by grepping (should produce zero output):

```bash
grep -rE "^from (config|db|embeddings|extraction|ingestion) " backend/pipeline/
```

- [ ] **Step 5: Fix the schema path in `pipeline.py`**

`init_db` currently accepts a string path defaulting to `"db/schema.sql"` from CWD. Make it default to a path relative to the package so it works from any CWD.

Modify `backend/pipeline/pipeline.py:35-39`:

```python
def init_db(schema_path: str | None = None) -> None:
    if schema_path is None:
        schema_path = str(Path(__file__).parent / "db" / "schema.sql")
    sql = Path(schema_path).read_text(encoding="utf-8")
    sql = sql.replace("__EMBEDDING_DIM__", str(settings.embedding_dimensions))
    with DBClient() as db:
        with db.cursor(commit=True) as cur:
            cur.execute(sql)
```

Also update the CLI argparse default in the same file (search for `init_db_parser.add_argument("--schema", default="db/schema.sql"`):

```python
init_db_parser.add_argument("--schema", default=None, help="Path to schema.sql (defaults to bundled)")
```

- [ ] **Step 6: Update `tests/test_canonicalizer.py` imports**

Replace at top of file:

```python
from extraction.canonicalizer import CharacterCanonicalizer, collect_character_names
```

With:

```python
from pipeline.extraction.canonicalizer import CharacterCanonicalizer, collect_character_names
```

- [ ] **Step 7: Reinstall the package and run tests**

```bash
uv sync --reinstall-package continuum
uv run pytest -q
```

Expected: `8 passed`.

- [ ] **Step 8: Smoke-test the CLI still works**

```bash
uv run novel-pipeline --help
uv run novel-pipeline list-novels
```

Expected: argparse help text and the JSON list of novels (the existing `de68d0c2-...` "PnP Canon Test").

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor: move pipeline into backend/pipeline package"
```

---

## Task 2: Add FastAPI dependencies and skeleton app

**Files:**
- Create: `backend/api/__init__.py`, `backend/api/app.py`
- Create: `backend/api/routes/__init__.py`
- Create: `tests/api/__init__.py`, `tests/api/test_health.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add backend dependencies**

```bash
uv add fastapi 'uvicorn[standard]'
```

Verify `fastapi` and `uvicorn` are now in `pyproject.toml` under `[project] dependencies`.

- [ ] **Step 2: Add the webapp script entry**

Append to `[project.scripts]` in `pyproject.toml`:

```toml
novel-webapp = "api.app:run"
```

- [ ] **Step 3: Write the failing test**

Create `tests/api/__init__.py` (empty file).

Create `tests/api/test_health.py`:

```python
from fastapi.testclient import TestClient

from api.app import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 4: Run the test to verify it fails**

```bash
uv run pytest tests/api/test_health.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'api'`.

- [ ] **Step 5: Create the FastAPI app**

Create `backend/api/__init__.py` (empty).

Create `backend/api/routes/__init__.py` (empty).

Create `backend/api/app.py`:

```python
from __future__ import annotations

import argparse

from fastapi import FastAPI

app = FastAPI(title="Continuum Wiki API")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def run() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Continuum wiki web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run("api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    run()
```

- [ ] **Step 6: Reinstall and run the test**

```bash
uv sync --reinstall-package continuum
uv run pytest tests/api/test_health.py -v
```

Expected: PASS.

- [ ] **Step 7: Smoke-test the server boots**

```bash
uv run novel-webapp --port 8765 &
SERVER_PID=$!
sleep 2
curl -s http://127.0.0.1:8765/api/health
echo
kill $SERVER_PID
```

Expected: `{"status":"ok"}`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(api): add FastAPI skeleton with health route"
```

---

## Task 3: Pydantic schemas + queries layer for novels

**Files:**
- Create: `backend/api/schemas.py`, `backend/api/queries.py`
- Create: `tests/api/conftest.py`, `tests/api/test_novels.py`
- Create: `backend/api/routes/novels.py`
- Modify: `backend/api/app.py` (mount the router)

- [ ] **Step 1: Write the failing tests**

Create `tests/api/conftest.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api import queries as queries_module


class FakeDB:
    """In-memory stand-in for db.client.DBClient for API tests."""

    def __init__(
        self,
        *,
        novels: list[dict[str, Any]] | None = None,
        chapters: list[dict[str, Any]] | None = None,
        characters: list[dict[str, Any]] | None = None,
        character_states: list[dict[str, Any]] | None = None,
        events: list[dict[str, Any]] | None = None,
        relationships: list[dict[str, Any]] | None = None,
        plot_threads: list[dict[str, Any]] | None = None,
        thread_events: list[dict[str, Any]] | None = None,
        continuity_flags: list[dict[str, Any]] | None = None,
        locations: list[dict[str, Any]] | None = None,
        objects: list[dict[str, Any]] | None = None,
    ) -> None:
        self.novels = novels or []
        self.chapters = chapters or []
        self.characters = characters or []
        self.character_states = character_states or []
        self.events = events or []
        self.relationships = relationships or []
        self.plot_threads = plot_threads or []
        self.thread_events = thread_events or []
        self.continuity_flags = continuity_flags or []
        self.locations = locations or []
        self.objects = objects or []

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeDB":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@pytest.fixture
def fake_db_factory(monkeypatch):
    """Return a factory that installs a FakeDB as the API's DB provider."""

    def _factory(**kwargs: Any) -> FakeDB:
        db = FakeDB(**kwargs)
        monkeypatch.setattr(queries_module, "_get_db", lambda: db)
        return db

    return _factory


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def make_novel(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "title": "Test Novel",
        "author": "Author",
        "language": "en",
        "created_at": datetime(2026, 1, 1),
    }
    base.update(overrides)
    return base


def make_chapter(novel_id: UUID, number: int, **overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "novel_id": novel_id,
        "number": number,
        "title": None,
        "summary": f"Summary of chapter {number}",
        "processed_at": datetime(2026, 1, number),
    }
    base.update(overrides)
    return base
```

Create `tests/api/test_novels.py`:

```python
from __future__ import annotations

from tests.api.conftest import make_chapter, make_novel


def test_list_novels_returns_max_chapter(fake_db_factory, client):
    novel = make_novel(title="My Novel")
    fake_db_factory(
        novels=[novel],
        chapters=[
            make_chapter(novel["id"], 1),
            make_chapter(novel["id"], 2),
            make_chapter(novel["id"], 3),
        ],
    )
    response = client.get("/api/novels")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["title"] == "My Novel"
    assert body[0]["max_chapter"] == 3


def test_list_novels_empty(fake_db_factory, client):
    fake_db_factory()
    response = client.get("/api/novels")
    assert response.status_code == 200
    assert response.json() == []


def test_get_novel(fake_db_factory, client):
    novel = make_novel(title="My Novel", author="Me")
    fake_db_factory(novels=[novel], chapters=[make_chapter(novel["id"], 1)])
    response = client.get(f"/api/novels/{novel['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "My Novel"
    assert body["author"] == "Me"
    assert body["max_chapter"] == 1


def test_get_novel_404(fake_db_factory, client):
    fake_db_factory()
    response = client.get("/api/novels/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/api/test_novels.py -v
```

Expected: FAIL — endpoints don't exist.

- [ ] **Step 3: Create the schemas**

Create `backend/api/schemas.py`:

```python
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NovelSummary(BaseModel):
    id: UUID
    title: str
    author: str | None = None
    language: str | None = None
    created_at: datetime
    max_chapter: int
```

- [ ] **Step 4: Create the queries layer**

Create `backend/api/queries.py`:

```python
from __future__ import annotations

from typing import Any
from uuid import UUID

from pipeline.db.client import DBClient


def _get_db() -> DBClient:
    """DB factory; tests patch this to return a FakeDB."""
    return DBClient()


def list_novels() -> list[dict[str, Any]]:
    db = _get_db()
    novels = db.novels if hasattr(db, "novels") else _list_novels_real(db)
    chapters = db.chapters if hasattr(db, "chapters") else None

    rows: list[dict[str, Any]] = []
    for novel in novels:
        novel_id = novel["id"]
        if chapters is not None:
            max_chapter = max(
                (c["number"] for c in chapters if c["novel_id"] == novel_id),
                default=0,
            )
        else:
            max_chapter = _max_chapter_real(db, novel_id)
        rows.append(
            {
                "id": novel_id,
                "title": novel["title"],
                "author": novel.get("author"),
                "language": novel.get("language"),
                "created_at": novel["created_at"],
                "max_chapter": max_chapter,
            }
        )
    return rows


def get_novel(novel_id: UUID) -> dict[str, Any] | None:
    db = _get_db()
    novels = db.novels if hasattr(db, "novels") else _list_novels_real(db)
    chapters = db.chapters if hasattr(db, "chapters") else None
    for novel in novels:
        if novel["id"] == novel_id:
            if chapters is not None:
                max_chapter = max(
                    (c["number"] for c in chapters if c["novel_id"] == novel_id),
                    default=0,
                )
            else:
                max_chapter = _max_chapter_real(db, novel_id)
            return {
                "id": novel_id,
                "title": novel["title"],
                "author": novel.get("author"),
                "language": novel.get("language"),
                "created_at": novel["created_at"],
                "max_chapter": max_chapter,
            }
    return None


def _list_novels_real(db: DBClient) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT id, title, author, language, created_at
        FROM novels
        ORDER BY created_at DESC
        """,
        dict_rows=True,
    )
    return [dict(r) for r in rows]


def _max_chapter_real(db: DBClient, novel_id: UUID) -> int:
    value = db.fetchval(
        "SELECT COALESCE(MAX(number), 0) FROM chapters WHERE novel_id = %s",
        (str(novel_id),),
    )
    return int(value or 0)
```

> **Note on the FakeDB fork:** the queries layer checks `hasattr(db, "novels")` to detect a FakeDB. This is the simplest pattern that lets us share one queries function between tests and prod. As we add more queries we'll keep this pattern — query function takes a DB, looks for in-memory attribute first, falls back to SQL.

- [ ] **Step 5: Create the novels router**

Create `backend/api/routes/novels.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from api import queries
from api.schemas import NovelSummary

router = APIRouter(prefix="/api/novels", tags=["novels"])


@router.get("", response_model=list[NovelSummary])
def list_novels() -> list[NovelSummary]:
    return [NovelSummary(**row) for row in queries.list_novels()]


@router.get("/{novel_id}", response_model=NovelSummary)
def get_novel(novel_id: UUID) -> NovelSummary:
    row = queries.get_novel(novel_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Novel not found")
    return NovelSummary(**row)
```

- [ ] **Step 6: Mount the router on the FastAPI app**

Modify `backend/api/app.py`. Add the import and `app.include_router` call:

```python
from __future__ import annotations

import argparse

from fastapi import FastAPI

from api.routes import novels

app = FastAPI(title="Continuum Wiki API")
app.include_router(novels.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def run() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Continuum wiki web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run("api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    run()
```

- [ ] **Step 7: Run the tests**

```bash
uv run pytest tests/api/test_novels.py -v
```

Expected: 4 PASS.

- [ ] **Step 8: Smoke-test against the real DB**

```bash
uv run novel-webapp --port 8765 &
SERVER_PID=$!
sleep 2
curl -s http://127.0.0.1:8765/api/novels | python3 -m json.tool
kill $SERVER_PID
```

Expected: a JSON array including the existing "PnP Canon Test" novel with `"max_chapter": 3`.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(api): novels list/detail endpoints"
```

---

## Task 4: Character routes — list and detail

**Files:**
- Modify: `backend/api/schemas.py`, `backend/api/queries.py`
- Create: `backend/api/routes/characters.py`
- Modify: `backend/api/app.py` (include the router)
- Create: `tests/api/test_characters.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_characters.py`:

```python
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_list_characters(fake_db_factory, client):
    novel = make_novel()
    char_id = uuid4()
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        characters=[
            {
                "id": char_id,
                "novel_id": novel["id"],
                "name": "Alice",
                "aliases": ["Allie"],
                "description": "Hero.",
                "first_appearance_chapter": 1,
            }
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/characters")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Alice"
    assert body[0]["aliases"] == ["Allie"]


def test_list_characters_respects_cap(fake_db_factory, client):
    """Characters whose first_appearance_chapter > cap are excluded."""
    novel = make_novel()
    early = uuid4()
    late = uuid4()
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1), make_chapter(novel["id"], 5)],
        characters=[
            {
                "id": early,
                "novel_id": novel["id"],
                "name": "Alice",
                "aliases": [],
                "description": None,
                "first_appearance_chapter": 1,
            },
            {
                "id": late,
                "novel_id": novel["id"],
                "name": "Bob",
                "aliases": [],
                "description": None,
                "first_appearance_chapter": 5,
            },
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/characters?cap=3")
    assert response.status_code == 200
    names = [row["name"] for row in response.json()]
    assert names == ["Alice"]


def test_character_detail_includes_states_within_cap(fake_db_factory, client):
    novel = make_novel()
    char_id = uuid4()
    chap1 = make_chapter(novel["id"], 1)
    chap5 = make_chapter(novel["id"], 5)
    fake_db_factory(
        novels=[novel],
        chapters=[chap1, chap5],
        characters=[
            {
                "id": char_id,
                "novel_id": novel["id"],
                "name": "Alice",
                "aliases": [],
                "description": "Hero.",
                "first_appearance_chapter": 1,
            }
        ],
        character_states=[
            {
                "id": uuid4(),
                "character_id": char_id,
                "chapter_id": chap1["id"],
                "location_id": None,
                "emotional_state": "calm",
                "goals": "explore",
                "knowledge": [],
                "relationships": {},
                "physical_state": "ok",
                "notes": None,
                "created_at": datetime(2026, 1, 1),
            },
            {
                "id": uuid4(),
                "character_id": char_id,
                "chapter_id": chap5["id"],
                "location_id": None,
                "emotional_state": "tense",
                "goals": "survive",
                "knowledge": [],
                "relationships": {},
                "physical_state": "wounded",
                "notes": None,
                "created_at": datetime(2026, 1, 5),
            },
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/characters/{char_id}?cap=3")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == "Alice"
    assert len(body["history"]) == 1
    assert body["history"][0]["chapter_number"] == 1
    assert body["current_state"]["chapter_number"] == 1
    assert body["events"] == []
    assert body["relationships"] == []


def test_character_detail_404(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[make_chapter(novel["id"], 1)])
    response = client.get(
        f"/api/novels/{novel['id']}/characters/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/api/test_characters.py -v
```

Expected: FAIL — endpoints not defined.

- [ ] **Step 3: Add character schemas**

Append to `backend/api/schemas.py`:

```python
from typing import Any


class CharacterSummary(BaseModel):
    id: UUID
    name: str
    aliases: list[str]
    description: str | None
    first_appearance_chapter: int | None


class CharacterStateRow(BaseModel):
    chapter_number: int
    location: str | None
    emotional_state: str | None
    goals: str | None
    knowledge: list[str]
    relationships: dict[str, Any]
    physical_state: str | None
    notes: str | None


class CharacterEventRow(BaseModel):
    id: UUID
    chapter_number: int
    description: str
    event_type: str | None
    impact_level: str | None
    involved_characters: list[str]
    involved_locations: list[str]
    involved_objects: list[str]


class CharacterRelationshipRow(BaseModel):
    chapter_number: int | None
    other_character_id: UUID
    other_character_name: str
    direction: str  # "from" if this character is entity_a; "to" if entity_b
    rel_type: str | None
    status: str | None
    notes: str | None


class CharacterDetail(BaseModel):
    identity: CharacterSummary
    current_state: CharacterStateRow | None
    history: list[CharacterStateRow]
    relationships: list[CharacterRelationshipRow]
    events: list[CharacterEventRow]
```

- [ ] **Step 4: Add character query helpers**

Append to `backend/api/queries.py`:

```python
def _max_chapter_for(db: Any, novel_id: UUID) -> int:
    if hasattr(db, "chapters"):
        return max((c["number"] for c in db.chapters if c["novel_id"] == novel_id), default=0)
    return _max_chapter_real(db, novel_id)


def _resolve_cap(db: Any, novel_id: UUID, cap: int | None) -> int:
    if cap is None:
        return _max_chapter_for(db, novel_id)
    return cap


def list_characters(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "characters"):
        rows = [
            c
            for c in db.characters
            if c["novel_id"] == novel_id
            and (
                c.get("first_appearance_chapter") is None
                or c["first_appearance_chapter"] <= effective_cap
            )
        ]
    else:
        rows = [
            dict(r)
            for r in db.fetchall(
                """
                SELECT id, name, aliases, description, first_appearance_chapter
                FROM characters
                WHERE novel_id = %s
                  AND (first_appearance_chapter IS NULL OR first_appearance_chapter <= %s)
                ORDER BY name
                """,
                (str(novel_id), effective_cap),
                dict_rows=True,
            )
        ]
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "aliases": list(r.get("aliases") or []),
            "description": r.get("description"),
            "first_appearance_chapter": r.get("first_appearance_chapter"),
        }
        for r in rows
    ]


def get_character_detail(novel_id: UUID, character_id: UUID, cap: int | None) -> dict[str, Any] | None:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "characters"):
        char = next(
            (c for c in db.characters if c["id"] == character_id and c["novel_id"] == novel_id),
            None,
        )
        if char is None:
            return None
        chapter_by_id = {c["id"]: c for c in db.chapters}
        states = [
            s
            for s in db.character_states
            if s["character_id"] == character_id
            and chapter_by_id[s["chapter_id"]]["number"] <= effective_cap
        ]
        states.sort(key=lambda s: chapter_by_id[s["chapter_id"]]["number"])
        events = [
            e
            for e in db.events
            if character_id in e.get("involved_characters", [])
            and chapter_by_id[e["chapter_id"]]["number"] <= effective_cap
        ]
        events.sort(key=lambda e: chapter_by_id[e["chapter_id"]]["number"])
        rels = [
            r
            for r in db.relationships
            if (r["entity_a_id"] == character_id or r["entity_b_id"] == character_id)
            and (r.get("chapter_id") is None or chapter_by_id[r["chapter_id"]]["number"] <= effective_cap)
        ]
        # Resolve names
        char_name = {c["id"]: c["name"] for c in db.characters}
        location_name = {l["id"]: l["name"] for l in db.locations}
        object_name = {o["id"]: o["name"] for o in db.objects}
        identity = {
            "id": char["id"],
            "name": char["name"],
            "aliases": list(char.get("aliases") or []),
            "description": char.get("description"),
            "first_appearance_chapter": char.get("first_appearance_chapter"),
        }
    else:
        identity_row = db.fetchone(
            "SELECT id, name, aliases, description, first_appearance_chapter FROM characters WHERE id = %s AND novel_id = %s",
            (str(character_id), str(novel_id)),
            dict_rows=True,
        )
        if identity_row is None:
            return None
        identity = {
            "id": identity_row["id"],
            "name": identity_row["name"],
            "aliases": list(identity_row.get("aliases") or []),
            "description": identity_row.get("description"),
            "first_appearance_chapter": identity_row.get("first_appearance_chapter"),
        }
        states_rows = db.fetchall(
            """
            SELECT cs.*, ch.number AS chapter_number
            FROM character_states cs
            JOIN chapters ch ON ch.id = cs.chapter_id
            WHERE cs.character_id = %s AND ch.number <= %s
            ORDER BY ch.number
            """,
            (str(character_id), effective_cap),
            dict_rows=True,
        )
        events_rows = db.fetchall(
            """
            SELECT e.*, ch.number AS chapter_number
            FROM events e
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE %s = ANY(e.involved_characters) AND ch.number <= %s
            ORDER BY ch.number, e.created_at
            """,
            (str(character_id), effective_cap),
            dict_rows=True,
        )
        rels_rows = db.fetchall(
            """
            SELECT r.*, ch.number AS chapter_number
            FROM relationships r
            LEFT JOIN chapters ch ON ch.id = r.chapter_id
            WHERE (r.entity_a_id = %s OR r.entity_b_id = %s)
              AND (ch.number IS NULL OR ch.number <= %s)
            """,
            (str(character_id), str(character_id), effective_cap),
            dict_rows=True,
        )
        # Need names for involved_*; do another fetch
        char_name_rows = db.fetchall("SELECT id, name FROM characters WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        char_name = {r["id"]: r["name"] for r in char_name_rows}
        loc_name_rows = db.fetchall("SELECT id, name FROM locations WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        location_name = {r["id"]: r["name"] for r in loc_name_rows}
        obj_name_rows = db.fetchall("SELECT id, name FROM objects WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        object_name = {r["id"]: r["name"] for r in obj_name_rows}

        chapter_by_id = {}  # not needed in real path
        states = [dict(r) for r in states_rows]
        events = [dict(r) for r in events_rows]
        rels = [dict(r) for r in rels_rows]

    def state_to_row(state: dict[str, Any]) -> dict[str, Any]:
        chapter_number = (
            chapter_by_id[state["chapter_id"]]["number"] if chapter_by_id else state.get("chapter_number")
        )
        loc_id = state.get("location_id")
        return {
            "chapter_number": chapter_number,
            "location": location_name.get(loc_id) if loc_id else None,
            "emotional_state": state.get("emotional_state"),
            "goals": state.get("goals"),
            "knowledge": list(state.get("knowledge") or []),
            "relationships": dict(state.get("relationships") or {}),
            "physical_state": state.get("physical_state"),
            "notes": state.get("notes"),
        }

    history = [state_to_row(s) for s in states]
    current_state = history[-1] if history else None

    def event_to_row(event: dict[str, Any]) -> dict[str, Any]:
        chapter_number = (
            chapter_by_id[event["chapter_id"]]["number"] if chapter_by_id else event.get("chapter_number")
        )
        return {
            "id": event["id"],
            "chapter_number": chapter_number,
            "description": event.get("description"),
            "event_type": event.get("event_type"),
            "impact_level": event.get("impact_level"),
            "involved_characters": [char_name.get(cid, str(cid)) for cid in event.get("involved_characters") or []],
            "involved_locations": [location_name.get(lid, str(lid)) for lid in event.get("involved_locations") or []],
            "involved_objects": [object_name.get(oid, str(oid)) for oid in event.get("involved_objects") or []],
        }

    def rel_to_row(rel: dict[str, Any]) -> dict[str, Any]:
        chapter_number = (
            chapter_by_id[rel["chapter_id"]]["number"]
            if chapter_by_id and rel.get("chapter_id")
            else rel.get("chapter_number")
        )
        if rel["entity_a_id"] == character_id:
            other = rel["entity_b_id"]
            direction = "from"
        else:
            other = rel["entity_a_id"]
            direction = "to"
        return {
            "chapter_number": chapter_number,
            "other_character_id": other,
            "other_character_name": char_name.get(other, str(other)),
            "direction": direction,
            "rel_type": rel.get("rel_type"),
            "status": rel.get("status"),
            "notes": rel.get("notes"),
        }

    return {
        "identity": identity,
        "current_state": current_state,
        "history": history,
        "relationships": [rel_to_row(r) for r in rels],
        "events": [event_to_row(e) for e in events],
    }
```

- [ ] **Step 5: Create the characters router**

Create `backend/api/routes/characters.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api import queries
from api.schemas import CharacterDetail, CharacterSummary

router = APIRouter(prefix="/api/novels/{novel_id}/characters", tags=["characters"])


@router.get("", response_model=list[CharacterSummary])
def list_characters(novel_id: UUID, cap: int | None = Query(default=None)) -> list[CharacterSummary]:
    return [CharacterSummary(**row) for row in queries.list_characters(novel_id, cap)]


@router.get("/{character_id}", response_model=CharacterDetail)
def get_character(
    novel_id: UUID,
    character_id: UUID,
    cap: int | None = Query(default=None),
) -> CharacterDetail:
    detail = queries.get_character_detail(novel_id, character_id, cap)
    if detail is None:
        raise HTTPException(status_code=404, detail="Character not found")
    return CharacterDetail(**detail)
```

- [ ] **Step 6: Mount the router**

Modify `backend/api/app.py` to import and include the characters router:

```python
from api.routes import characters, novels

app = FastAPI(title="Continuum Wiki API")
app.include_router(novels.router)
app.include_router(characters.router)
```

- [ ] **Step 7: Run the tests**

```bash
uv run pytest tests/api/test_characters.py -v
```

Expected: 4 PASS.

- [ ] **Step 8: Smoke-test against real DB**

```bash
uv run novel-webapp --port 8765 &
SERVER_PID=$!
sleep 2
NOVEL_ID=$(curl -s http://127.0.0.1:8765/api/novels | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['id'])")
curl -s "http://127.0.0.1:8765/api/novels/$NOVEL_ID/characters?cap=2" | python3 -m json.tool | head -30
kill $SERVER_PID
```

Expected: a JSON array of characters who first appeared in chapters 1–2 (Mr. Bennet, Lizzy, etc.; not Mr. Darcy who first appears in chapter 3).

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(api): character list and detail with chapter cap"
```

---

## Task 5: Chapters, timeline, threads, continuity routes

**Files:**
- Modify: `backend/api/schemas.py`, `backend/api/queries.py`, `backend/api/app.py`
- Create: `backend/api/routes/chapters.py`, `backend/api/routes/timeline.py`, `backend/api/routes/threads.py`, `backend/api/routes/continuity.py`
- Create: `tests/api/test_chapters.py`, `tests/api/test_timeline.py`, `tests/api/test_threads.py`, `tests/api/test_continuity.py`

This task follows the same pattern as Task 4: schema + query + router + test. Each route honors `cap`. To avoid bloating this document with repeated boilerplate, the test for each route is shown; the implementation pattern is identical to Task 4 (resolve cap, filter by chapter number, return Pydantic models).

- [ ] **Step 1: Add schemas**

Append to `backend/api/schemas.py`:

```python
class ChapterSummary(BaseModel):
    id: UUID
    number: int
    title: str | None
    summary: str | None
    processed_at: datetime | None


class TimelineEvent(BaseModel):
    id: UUID
    chapter_number: int
    description: str
    event_type: str | None
    impact_level: str | None
    involved_characters: list[str]
    involved_locations: list[str]
    involved_objects: list[str]


class ThreadEventLink(BaseModel):
    event_id: UUID
    description: str
    chapter_number: int
    impact: str | None


class PlotThread(BaseModel):
    id: UUID
    title: str
    description: str | None
    status: str
    thread_type: str | None
    opened_chapter: int | None
    closed_chapter: int | None
    events: list[ThreadEventLink]


class ContinuityFlag(BaseModel):
    id: UUID
    chapter_number: int
    description: str
    flag_type: str | None
    resolved: bool
    resolved_chapter_number: int | None
```

- [ ] **Step 2: Add query helpers**

Append to `backend/api/queries.py`. Each helper follows the Task 4 pattern (FakeDB branch + SQL branch + cap filter):

```python
def list_chapters(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "chapters"):
        rows = [
            c for c in db.chapters
            if c["novel_id"] == novel_id and c["number"] <= effective_cap
        ]
    else:
        rows = [
            dict(r) for r in db.fetchall(
                "SELECT id, number, title, summary, processed_at FROM chapters WHERE novel_id = %s AND number <= %s ORDER BY number",
                (str(novel_id), effective_cap),
                dict_rows=True,
            )
        ]
    rows.sort(key=lambda r: r["number"])
    return [
        {
            "id": r["id"],
            "number": r["number"],
            "title": r.get("title"),
            "summary": r.get("summary"),
            "processed_at": r.get("processed_at"),
        }
        for r in rows
    ]


def list_timeline(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "chapters"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        char_name = {c["id"]: c["name"] for c in db.characters}
        loc_name = {l["id"]: l["name"] for l in db.locations}
        obj_name = {o["id"]: o["name"] for o in db.objects}
        rows: list[dict[str, Any]] = []
        for e in db.events:
            ch = chapter_by_id.get(e["chapter_id"])
            if ch is None or ch["number"] > effective_cap:
                continue
            rows.append(
                {
                    "id": e["id"],
                    "chapter_number": ch["number"],
                    "description": e["description"],
                    "event_type": e.get("event_type"),
                    "impact_level": e.get("impact_level"),
                    "involved_characters": [char_name.get(c, str(c)) for c in e.get("involved_characters") or []],
                    "involved_locations": [loc_name.get(l, str(l)) for l in e.get("involved_locations") or []],
                    "involved_objects": [obj_name.get(o, str(o)) for o in e.get("involved_objects") or []],
                }
            )
        rows.sort(key=lambda r: r["chapter_number"])
        return rows
    raw = db.fetchall(
        """
        SELECT e.id, e.description, e.event_type, e.impact_level,
               e.involved_characters, e.involved_locations, e.involved_objects,
               ch.number AS chapter_number
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
        ORDER BY ch.number, e.created_at
        """,
        (str(novel_id), effective_cap),
        dict_rows=True,
    )
    char_rows = db.fetchall("SELECT id, name FROM characters WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
    char_name = {r["id"]: r["name"] for r in char_rows}
    loc_rows = db.fetchall("SELECT id, name FROM locations WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
    loc_name = {r["id"]: r["name"] for r in loc_rows}
    obj_rows = db.fetchall("SELECT id, name FROM objects WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
    obj_name = {r["id"]: r["name"] for r in obj_rows}
    return [
        {
            "id": r["id"],
            "chapter_number": r["chapter_number"],
            "description": r["description"],
            "event_type": r.get("event_type"),
            "impact_level": r.get("impact_level"),
            "involved_characters": [char_name.get(c, str(c)) for c in (r.get("involved_characters") or [])],
            "involved_locations": [loc_name.get(l, str(l)) for l in (r.get("involved_locations") or [])],
            "involved_objects": [obj_name.get(o, str(o)) for o in (r.get("involved_objects") or [])],
        }
        for r in raw
    ]


def list_threads(novel_id: UUID, cap: int | None, status: str) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "chapters"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        threads: list[dict[str, Any]] = []
        for t in db.plot_threads:
            if t["novel_id"] != novel_id:
                continue
            if t.get("opened_chapter") is not None and t["opened_chapter"] > effective_cap:
                continue
            effective_status = t["status"]
            closed_chapter = t.get("closed_chapter")
            if effective_status == "closed" and closed_chapter is not None and closed_chapter > effective_cap:
                effective_status = "progressing"
                closed_chapter = None
            if status != "all" and effective_status != status:
                continue
            event_links: list[dict[str, Any]] = []
            for te in db.thread_events:
                if te["thread_id"] != t["id"]:
                    continue
                evt = next((e for e in db.events if e["id"] == te["event_id"]), None)
                if evt is None:
                    continue
                ch = chapter_by_id.get(evt["chapter_id"])
                if ch is None or ch["number"] > effective_cap:
                    continue
                event_links.append(
                    {
                        "event_id": evt["id"],
                        "description": evt["description"],
                        "chapter_number": ch["number"],
                        "impact": te.get("impact"),
                    }
                )
            threads.append(
                {
                    "id": t["id"],
                    "title": t["title"],
                    "description": t.get("description"),
                    "status": effective_status,
                    "thread_type": t.get("thread_type"),
                    "opened_chapter": t.get("opened_chapter"),
                    "closed_chapter": closed_chapter,
                    "events": event_links,
                }
            )
        return threads

    threads_raw = db.fetchall(
        """
        SELECT id, title, description, status, thread_type, opened_chapter, closed_chapter
        FROM plot_threads
        WHERE novel_id = %s AND (opened_chapter IS NULL OR opened_chapter <= %s)
        ORDER BY opened_chapter NULLS LAST, title
        """,
        (str(novel_id), effective_cap),
        dict_rows=True,
    )
    threads: list[dict[str, Any]] = []
    for t in threads_raw:
        effective_status = t["status"]
        closed_chapter = t.get("closed_chapter")
        if effective_status == "closed" and closed_chapter is not None and closed_chapter > effective_cap:
            effective_status = "progressing"
            closed_chapter = None
        if status != "all" and effective_status != status:
            continue
        event_links_raw = db.fetchall(
            """
            SELECT te.event_id, te.impact, e.description, ch.number AS chapter_number
            FROM thread_events te
            JOIN events e ON e.id = te.event_id
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE te.thread_id = %s AND ch.number <= %s
            ORDER BY ch.number
            """,
            (str(t["id"]), effective_cap),
            dict_rows=True,
        )
        threads.append(
            {
                "id": t["id"],
                "title": t["title"],
                "description": t.get("description"),
                "status": effective_status,
                "thread_type": t.get("thread_type"),
                "opened_chapter": t.get("opened_chapter"),
                "closed_chapter": closed_chapter,
                "events": [dict(r) for r in event_links_raw],
            }
        )
    return threads


def list_continuity(novel_id: UUID, cap: int | None, resolved_filter: str) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "chapters"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        flags: list[dict[str, Any]] = []
        for f in db.continuity_flags:
            ch = chapter_by_id.get(f["chapter_id"])
            if ch is None or ch["number"] > effective_cap:
                continue
            resolved_at_id = f.get("resolved_chapter_id")
            resolved_chapter_number = (
                chapter_by_id[resolved_at_id]["number"]
                if resolved_at_id and resolved_at_id in chapter_by_id
                else None
            )
            effectively_resolved = bool(f.get("resolved")) and (
                resolved_chapter_number is None or resolved_chapter_number <= effective_cap
            )
            if resolved_filter == "open" and effectively_resolved:
                continue
            flags.append(
                {
                    "id": f["id"],
                    "chapter_number": ch["number"],
                    "description": f["description"],
                    "flag_type": f.get("flag_type"),
                    "resolved": effectively_resolved,
                    "resolved_chapter_number": resolved_chapter_number if effectively_resolved else None,
                }
            )
        return flags
    raw = db.fetchall(
        """
        SELECT cf.id, cf.description, cf.flag_type, cf.resolved, cf.resolved_chapter_id,
               ch.number AS chapter_number
        FROM continuity_flags cf
        JOIN chapters ch ON ch.id = cf.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
        ORDER BY ch.number
        """,
        (str(novel_id), effective_cap),
        dict_rows=True,
    )
    chap_lookup = {
        r["id"]: r["number"]
        for r in db.fetchall(
            "SELECT id, number FROM chapters WHERE novel_id = %s", (str(novel_id),), dict_rows=True
        )
    }
    out: list[dict[str, Any]] = []
    for r in raw:
        resolved_at_id = r.get("resolved_chapter_id")
        resolved_chapter_number = chap_lookup.get(resolved_at_id)
        effectively_resolved = bool(r.get("resolved")) and (
            resolved_chapter_number is None or resolved_chapter_number <= effective_cap
        )
        if resolved_filter == "open" and effectively_resolved:
            continue
        out.append(
            {
                "id": r["id"],
                "chapter_number": r["chapter_number"],
                "description": r["description"],
                "flag_type": r.get("flag_type"),
                "resolved": effectively_resolved,
                "resolved_chapter_number": resolved_chapter_number if effectively_resolved else None,
            }
        )
    return out
```

- [ ] **Step 3: Create the four routers**

Create `backend/api/routes/chapters.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import ChapterSummary

router = APIRouter(prefix="/api/novels/{novel_id}/chapters", tags=["chapters"])


@router.get("", response_model=list[ChapterSummary])
def list_chapters(novel_id: UUID, cap: int | None = Query(default=None)) -> list[ChapterSummary]:
    return [ChapterSummary(**row) for row in queries.list_chapters(novel_id, cap)]
```

Create `backend/api/routes/timeline.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import TimelineEvent

router = APIRouter(prefix="/api/novels/{novel_id}/timeline", tags=["timeline"])


@router.get("", response_model=list[TimelineEvent])
def list_timeline(novel_id: UUID, cap: int | None = Query(default=None)) -> list[TimelineEvent]:
    return [TimelineEvent(**row) for row in queries.list_timeline(novel_id, cap)]
```

Create `backend/api/routes/threads.py`:

```python
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import PlotThread

router = APIRouter(prefix="/api/novels/{novel_id}/threads", tags=["threads"])


@router.get("", response_model=list[PlotThread])
def list_threads(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    status: Literal["open", "progressing", "closed", "all"] = Query(default="all"),
) -> list[PlotThread]:
    return [PlotThread(**row) for row in queries.list_threads(novel_id, cap, status)]
```

Create `backend/api/routes/continuity.py`:

```python
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import ContinuityFlag

router = APIRouter(prefix="/api/novels/{novel_id}/continuity", tags=["continuity"])


@router.get("", response_model=list[ContinuityFlag])
def list_continuity(
    novel_id: UUID,
    cap: int | None = Query(default=None),
    resolved: Literal["open", "all"] = Query(default="all"),
) -> list[ContinuityFlag]:
    return [ContinuityFlag(**row) for row in queries.list_continuity(novel_id, cap, resolved)]
```

- [ ] **Step 4: Mount the new routers**

Modify `backend/api/app.py`:

```python
from api.routes import characters, chapters, continuity, novels, threads, timeline

app = FastAPI(title="Continuum Wiki API")
app.include_router(novels.router)
app.include_router(characters.router)
app.include_router(chapters.router)
app.include_router(timeline.router)
app.include_router(threads.router)
app.include_router(continuity.router)
```

- [ ] **Step 5: Write smoke tests for each new route**

Create `tests/api/test_chapters.py`:

```python
from __future__ import annotations

from tests.api.conftest import make_chapter, make_novel


def test_list_chapters_filters_by_cap(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], n) for n in (1, 2, 3, 4, 5)],
    )
    response = client.get(f"/api/novels/{novel['id']}/chapters?cap=3")
    assert response.status_code == 200
    numbers = [r["number"] for r in response.json()]
    assert numbers == [1, 2, 3]
```

Create `tests/api/test_timeline.py`:

```python
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_timeline_filters_by_cap_and_resolves_names(fake_db_factory, client):
    novel = make_novel()
    char_id = uuid4()
    chap1 = make_chapter(novel["id"], 1)
    chap5 = make_chapter(novel["id"], 5)
    fake_db_factory(
        novels=[novel],
        chapters=[chap1, chap5],
        characters=[
            {
                "id": char_id,
                "novel_id": novel["id"],
                "name": "Alice",
                "aliases": [],
                "description": None,
                "first_appearance_chapter": 1,
            }
        ],
        events=[
            {
                "id": uuid4(),
                "chapter_id": chap1["id"],
                "description": "Alice arrives.",
                "event_type": "arrival",
                "impact_level": "medium",
                "involved_characters": [char_id],
                "involved_locations": [],
                "involved_objects": [],
                "created_at": datetime(2026, 1, 1),
            },
            {
                "id": uuid4(),
                "chapter_id": chap5["id"],
                "description": "Alice leaves.",
                "event_type": "action",
                "impact_level": "low",
                "involved_characters": [char_id],
                "involved_locations": [],
                "involved_objects": [],
                "created_at": datetime(2026, 1, 5),
            },
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/timeline?cap=3")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["description"] == "Alice arrives."
    assert rows[0]["involved_characters"] == ["Alice"]
```

Create `tests/api/test_threads.py`:

```python
from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_threads_status_filter(fake_db_factory, client):
    novel = make_novel()
    open_thread = {
        "id": uuid4(),
        "novel_id": novel["id"],
        "title": "Open",
        "description": None,
        "status": "open",
        "thread_type": "goal",
        "opened_chapter": 1,
        "closed_chapter": None,
    }
    closed_thread = {
        "id": uuid4(),
        "novel_id": novel["id"],
        "title": "Closed",
        "description": None,
        "status": "closed",
        "thread_type": "mystery",
        "opened_chapter": 1,
        "closed_chapter": 2,
    }
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1), make_chapter(novel["id"], 2)],
        plot_threads=[open_thread, closed_thread],
    )
    response = client.get(f"/api/novels/{novel['id']}/threads?status=open")
    titles = [r["title"] for r in response.json()]
    assert titles == ["Open"]


def test_threads_closed_in_future_chapter_appears_open(fake_db_factory, client):
    novel = make_novel()
    thread = {
        "id": uuid4(),
        "novel_id": novel["id"],
        "title": "T",
        "description": None,
        "status": "closed",
        "thread_type": None,
        "opened_chapter": 1,
        "closed_chapter": 5,
    }
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], n) for n in (1, 5)],
        plot_threads=[thread],
    )
    response = client.get(f"/api/novels/{novel['id']}/threads?cap=3")
    rows = response.json()
    assert rows[0]["status"] == "progressing"
    assert rows[0]["closed_chapter"] is None
```

Create `tests/api/test_continuity.py`:

```python
from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_continuity_resolved_filter(fake_db_factory, client):
    novel = make_novel()
    chap1 = make_chapter(novel["id"], 1)
    chap2 = make_chapter(novel["id"], 2)
    fake_db_factory(
        novels=[novel],
        chapters=[chap1, chap2],
        continuity_flags=[
            {
                "id": uuid4(),
                "chapter_id": chap1["id"],
                "description": "open flag",
                "flag_type": "foreshadowing",
                "resolved": False,
                "resolved_chapter_id": None,
            },
            {
                "id": uuid4(),
                "chapter_id": chap1["id"],
                "description": "resolved flag",
                "flag_type": "setup",
                "resolved": True,
                "resolved_chapter_id": chap2["id"],
            },
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/continuity?resolved=open")
    descriptions = [r["description"] for r in response.json()]
    assert descriptions == ["open flag"]
```

- [ ] **Step 6: Run all API tests**

```bash
uv run pytest tests/api/ -v
```

Expected: all tests pass. (Original 8 canonicalizer tests + ~14 new API tests.)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(api): chapters, timeline, threads, continuity routes"
```

---

## Task 6: Relationships graph endpoint

**Files:**
- Modify: `backend/api/schemas.py`, `backend/api/queries.py`, `backend/api/app.py`
- Create: `backend/api/routes/relationships.py`, `tests/api/test_relationships.py`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_relationships.py`:

```python
from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_relationships_graph_returns_nodes_and_edges(fake_db_factory, client):
    novel = make_novel()
    a = uuid4()
    b = uuid4()
    chap1 = make_chapter(novel["id"], 1)
    fake_db_factory(
        novels=[novel],
        chapters=[chap1],
        characters=[
            {"id": a, "novel_id": novel["id"], "name": "Alice", "aliases": [], "description": None, "first_appearance_chapter": 1},
            {"id": b, "novel_id": novel["id"], "name": "Bob", "aliases": [], "description": None, "first_appearance_chapter": 1},
        ],
        relationships=[
            {
                "id": uuid4(),
                "entity_a_id": a,
                "entity_a_type": "character",
                "entity_b_id": b,
                "entity_b_type": "character",
                "rel_type": "friend",
                "status": "active",
                "chapter_id": chap1["id"],
                "notes": None,
            }
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/relationships")
    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) == 2
    assert {n["label"] for n in body["nodes"]} == {"Alice", "Bob"}
    assert len(body["edges"]) == 1
    edge = body["edges"][0]
    assert edge["label"] == "friend"
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/api/test_relationships.py -v
```

Expected: FAIL.

- [ ] **Step 3: Add schemas**

Append to `backend/api/schemas.py`:

```python
class GraphNode(BaseModel):
    id: UUID
    label: str
    description: str | None


class GraphEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    from_: UUID = Field(alias="from")
    to: UUID
    label: str | None
    chapter_number: int | None


class RelationshipGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
```

- [ ] **Step 4: Add the query helper**

Append to `backend/api/queries.py`:

```python
def get_relationship_graph(novel_id: UUID, cap: int | None) -> dict[str, Any]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "chapters"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        characters = [
            c for c in db.characters
            if c["novel_id"] == novel_id
            and (c.get("first_appearance_chapter") is None or c["first_appearance_chapter"] <= effective_cap)
        ]
        rels = [
            r for r in db.relationships
            if (r.get("chapter_id") is None or chapter_by_id.get(r["chapter_id"], {}).get("number", 999999) <= effective_cap)
            and r["entity_a_type"] == "character" and r["entity_b_type"] == "character"
        ]
        rel_chapter = lambda r: chapter_by_id[r["chapter_id"]]["number"] if r.get("chapter_id") else None
    else:
        characters = [
            dict(r) for r in db.fetchall(
                "SELECT id, name, description, first_appearance_chapter FROM characters WHERE novel_id = %s AND (first_appearance_chapter IS NULL OR first_appearance_chapter <= %s)",
                (str(novel_id), effective_cap),
                dict_rows=True,
            )
        ]
        rels_raw = db.fetchall(
            """
            SELECT r.*, ch.number AS chapter_number
            FROM relationships r
            LEFT JOIN chapters ch ON ch.id = r.chapter_id
            WHERE r.entity_a_type = 'character' AND r.entity_b_type = 'character'
              AND (ch.number IS NULL OR ch.number <= %s)
              AND EXISTS (SELECT 1 FROM characters c WHERE c.id = r.entity_a_id AND c.novel_id = %s)
            """,
            (effective_cap, str(novel_id)),
            dict_rows=True,
        )
        rels = [dict(r) for r in rels_raw]
        rel_chapter = lambda r: r.get("chapter_number")

    nodes = [
        {"id": c["id"], "label": c["name"], "description": c.get("description")}
        for c in characters
    ]
    character_id_set = {c["id"] for c in characters}
    edges = [
        {
            "id": r["id"],
            "from": r["entity_a_id"],
            "to": r["entity_b_id"],
            "label": r.get("rel_type"),
            "chapter_number": rel_chapter(r),
        }
        for r in rels
        if r["entity_a_id"] in character_id_set and r["entity_b_id"] in character_id_set
    ]
    return {"nodes": nodes, "edges": edges}
```

- [ ] **Step 5: Create the router**

Create `backend/api/routes/relationships.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from api import queries
from api.schemas import RelationshipGraph

router = APIRouter(prefix="/api/novels/{novel_id}/relationships", tags=["relationships"])


@router.get("", response_model=RelationshipGraph)
def get_relationships(novel_id: UUID, cap: int | None = Query(default=None)) -> RelationshipGraph:
    data = queries.get_relationship_graph(novel_id, cap)
    return RelationshipGraph.model_validate(data)
```

- [ ] **Step 6: Mount and run**

Update `backend/api/app.py` to import and include `relationships.router`.

```bash
uv run pytest tests/api/test_relationships.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(api): relationships graph endpoint"
```

---

## Task 7: Frontend scaffolding (Vite + React + TS)

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/index.html`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`, `frontend/src/api.ts`
- Create: `frontend/src/components/Layout.tsx`, `frontend/src/components/Sidebar.tsx`
- Create: `frontend/src/routes/Novels.tsx`
- Create: `frontend/src/hooks/useChapterCap.ts`

- [ ] **Step 1: Initialize Vite project**

```bash
cd frontend
npm create vite@latest . -- --template react-ts
```

When prompted, accept the defaults (overwrite directory). This creates `package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, and `src/`.

- [ ] **Step 2: Install dependencies**

```bash
cd frontend
npm install react-router-dom @tanstack/react-query vis-network vis-data
npm install --save-dev vitest @testing-library/react @testing-library/jest-dom jsdom
```

- [ ] **Step 3: Configure Vite proxy**

Replace `frontend/vite.config.ts` with:

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
```

- [ ] **Step 4: Replace generated `App.tsx` with router scaffold**

Overwrite `frontend/src/App.tsx`:

```typescript
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Novels from "./routes/Novels";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000 } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <Routes>
          <Route path="/" element={<Navigate to="/novels" replace />} />
          <Route path="/novels" element={<Layout><Novels /></Layout>} />
        </Routes>
      </Router>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 5: Replace generated `main.tsx`**

Overwrite `frontend/src/main.tsx`:

```typescript
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 6: Create the typed API client**

Create `frontend/src/api.ts`:

```typescript
export type Novel = {
  id: string;
  title: string;
  author: string | null;
  language: string | null;
  created_at: string;
  max_chapter: number;
};

export type CharacterSummary = {
  id: string;
  name: string;
  aliases: string[];
  description: string | null;
  first_appearance_chapter: number | null;
};

export type CharacterStateRow = {
  chapter_number: number;
  location: string | null;
  emotional_state: string | null;
  goals: string | null;
  knowledge: string[];
  relationships: Record<string, string>;
  physical_state: string | null;
  notes: string | null;
};

export type CharacterEventRow = {
  id: string;
  chapter_number: number;
  description: string;
  event_type: string | null;
  impact_level: string | null;
  involved_characters: string[];
  involved_locations: string[];
  involved_objects: string[];
};

export type CharacterRelationshipRow = {
  chapter_number: number | null;
  other_character_id: string;
  other_character_name: string;
  direction: "from" | "to";
  rel_type: string | null;
  status: string | null;
  notes: string | null;
};

export type CharacterDetail = {
  identity: CharacterSummary;
  current_state: CharacterStateRow | null;
  history: CharacterStateRow[];
  relationships: CharacterRelationshipRow[];
  events: CharacterEventRow[];
};

export type ChapterSummary = {
  id: string;
  number: number;
  title: string | null;
  summary: string | null;
  processed_at: string | null;
};

export type TimelineEvent = CharacterEventRow;

export type ThreadEventLink = {
  event_id: string;
  description: string;
  chapter_number: number;
  impact: string | null;
};

export type PlotThread = {
  id: string;
  title: string;
  description: string | null;
  status: string;
  thread_type: string | null;
  opened_chapter: number | null;
  closed_chapter: number | null;
  events: ThreadEventLink[];
};

export type ContinuityFlag = {
  id: string;
  chapter_number: number;
  description: string;
  flag_type: string | null;
  resolved: boolean;
  resolved_chapter_number: number | null;
};

export type GraphNode = { id: string; label: string; description: string | null };
export type GraphEdge = { id: string; from: string; to: string; label: string | null; chapter_number: number | null };
export type RelationshipGraph = { nodes: GraphNode[]; edges: GraphEdge[] };

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

const capParam = (cap: number | null) => (cap == null ? "" : `?cap=${cap}`);

export const api = {
  novels: () => fetchJson<Novel[]>("/api/novels"),
  novel: (id: string) => fetchJson<Novel>(`/api/novels/${id}`),
  characters: (id: string, cap: number | null) =>
    fetchJson<CharacterSummary[]>(`/api/novels/${id}/characters${capParam(cap)}`),
  character: (novelId: string, characterId: string, cap: number | null) =>
    fetchJson<CharacterDetail>(`/api/novels/${novelId}/characters/${characterId}${capParam(cap)}`),
  chapters: (id: string, cap: number | null) =>
    fetchJson<ChapterSummary[]>(`/api/novels/${id}/chapters${capParam(cap)}`),
  timeline: (id: string, cap: number | null) =>
    fetchJson<TimelineEvent[]>(`/api/novels/${id}/timeline${capParam(cap)}`),
  threads: (id: string, cap: number | null, status: string) => {
    const params = new URLSearchParams();
    if (cap != null) params.set("cap", String(cap));
    params.set("status", status);
    return fetchJson<PlotThread[]>(`/api/novels/${id}/threads?${params}`);
  },
  continuity: (id: string, cap: number | null, resolved: string) => {
    const params = new URLSearchParams();
    if (cap != null) params.set("cap", String(cap));
    params.set("resolved", resolved);
    return fetchJson<ContinuityFlag[]>(`/api/novels/${id}/continuity?${params}`);
  },
  relationships: (id: string, cap: number | null) =>
    fetchJson<RelationshipGraph>(`/api/novels/${id}/relationships${capParam(cap)}`),
};
```

- [ ] **Step 7: Create the chapter cap hook**

Create `frontend/src/hooks/useChapterCap.ts`:

```typescript
import { useSearchParams } from "react-router-dom";

export function useChapterCap(): [number | null, (next: number | null) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("cap");
  const cap = raw == null || raw === "" ? null : Number(raw);
  const setCap = (next: number | null) => {
    const newParams = new URLSearchParams(params);
    if (next == null) {
      newParams.delete("cap");
    } else {
      newParams.set("cap", String(next));
    }
    setParams(newParams);
  };
  return [cap, setCap];
}
```

- [ ] **Step 8: Create Layout and Sidebar components**

Create `frontend/src/components/Sidebar.tsx`:

```typescript
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { useChapterCap } from "../hooks/useChapterCap";
import { api } from "../api";

export default function Sidebar() {
  const { novelId } = useParams();
  const location = useLocation();
  const [cap, setCap] = useChapterCap();
  const novelQuery = useQuery({
    queryKey: ["novel", novelId],
    queryFn: () => api.novel(novelId!),
    enabled: Boolean(novelId),
  });

  const max = novelQuery.data?.max_chapter ?? null;
  const effective = cap ?? max ?? 0;

  const links = novelId
    ? [
        ["Characters", `/novels/${novelId}/characters`],
        ["Chapters", `/novels/${novelId}/chapters`],
        ["Timeline", `/novels/${novelId}/timeline`],
        ["Threads", `/novels/${novelId}/threads`],
        ["Relationships", `/novels/${novelId}/relationships`],
        ["Continuity", `/novels/${novelId}/continuity`],
      ]
    : [];

  return (
    <aside className="sidebar">
      <h1>
        <Link to="/novels">Continuum</Link>
      </h1>
      {novelQuery.data && <h2>{novelQuery.data.title}</h2>}
      <nav>
        <ul>
          {links.map(([label, to]) => (
            <li key={to}>
              <Link
                to={`${to}${location.search}`}
                className={location.pathname.startsWith(to) ? "active" : ""}
              >
                {label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      {max != null && (
        <div className="chapter-cap">
          <label htmlFor="cap-slider">As of chapter {effective}</label>
          <input
            id="cap-slider"
            type="range"
            min={1}
            max={max}
            value={effective}
            onChange={(e) => setCap(Number(e.target.value))}
          />
          <button onClick={() => setCap(null)}>Show all</button>
        </div>
      )}
    </aside>
  );
}
```

Create `frontend/src/components/Layout.tsx`:

```typescript
import type { ReactNode } from "react";
import Sidebar from "./Sidebar";

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="layout">
      <Sidebar />
      <main className="content">{children}</main>
    </div>
  );
}
```

- [ ] **Step 9: Create the Novels list page**

Create `frontend/src/routes/Novels.tsx`:

```typescript
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";

export default function Novels() {
  const { data, isLoading, error } = useQuery({ queryKey: ["novels"], queryFn: api.novels });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <p>No novels.</p>;
  return (
    <div>
      <h1>Novels</h1>
      <ul>
        {data.map((n) => (
          <li key={n.id}>
            <Link to={`/novels/${n.id}/characters`}>{n.title}</Link>
            {" — "}
            {n.author ?? "Unknown"} · {n.max_chapter} chapter{n.max_chapter === 1 ? "" : "s"}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 10: Add minimal CSS**

Create `frontend/src/styles.css`:

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, system-ui, sans-serif; color: #111; background: #fafafa; }
a { color: #1a73e8; text-decoration: none; }
a:hover { text-decoration: underline; }
.layout { display: flex; min-height: 100vh; }
.sidebar { width: 240px; padding: 16px; background: #f0f0f0; border-right: 1px solid #ddd; }
.sidebar h1 { font-size: 18px; margin: 0 0 16px; }
.sidebar h2 { font-size: 14px; color: #666; font-weight: normal; margin: 0 0 16px; }
.sidebar nav ul { list-style: none; padding: 0; margin: 0; }
.sidebar nav li { margin: 4px 0; }
.sidebar nav a.active { font-weight: bold; }
.chapter-cap { margin-top: 24px; padding-top: 16px; border-top: 1px solid #ddd; }
.chapter-cap label { display: block; font-size: 12px; margin-bottom: 8px; }
.chapter-cap input[type="range"] { width: 100%; }
.chapter-cap button { margin-top: 8px; font-size: 11px; }
.content { flex: 1; padding: 24px; max-width: 1200px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; vertical-align: top; }
th { background: #f5f5f5; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
.field-list { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; }
.field-list dt { font-weight: bold; color: #555; }
.field-list dd { margin: 0; }
.tag { display: inline-block; padding: 2px 8px; background: #eef; border-radius: 4px; font-size: 12px; margin-right: 4px; }
.muted { color: #999; }
```

- [ ] **Step 11: Smoke-test the dev server**

```bash
# Terminal 1: API
uv run novel-webapp --port 8000 &
API_PID=$!

# Terminal 2 (or new shell)
cd frontend
npm run dev &
VITE_PID=$!
sleep 3

curl -s http://127.0.0.1:5173/api/novels | head -c 200
echo

kill $VITE_PID $API_PID
```

Expected: the curl through Vite's proxy should return JSON novels (proves the proxy + API + frontend dev server all line up).

Open `http://localhost:5173` in a browser; the novel list should render.

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "feat(frontend): scaffold Vite+React+TS, novel list page"
```

---

## Task 8: Frontend character views

**Files:**
- Create: `frontend/src/components/FieldList.tsx`
- Create: `frontend/src/routes/CharacterList.tsx`, `frontend/src/routes/CharacterDetail.tsx`
- Modify: `frontend/src/App.tsx` (register new routes)

- [ ] **Step 1: Create the labeled field renderer**

Create `frontend/src/components/FieldList.tsx`:

```typescript
import type { ReactNode } from "react";

type Field = { label: string; value: ReactNode };

export default function FieldList({ fields }: { fields: Field[] }) {
  return (
    <dl className="field-list">
      {fields.map(({ label, value }) => (
        <div key={label} style={{ display: "contents" }}>
          <dt>{label}</dt>
          <dd>{value ?? <span className="muted">—</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

export function renderArray(values: string[] | null | undefined): ReactNode {
  if (!values || values.length === 0) return <span className="muted">—</span>;
  return values.map((v) => (
    <span key={v} className="tag">
      {v}
    </span>
  ));
}

export function renderJson(obj: Record<string, unknown> | null | undefined): ReactNode {
  if (!obj || Object.keys(obj).length === 0) return <span className="muted">—</span>;
  return <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(obj, null, 2)}</pre>;
}
```

- [ ] **Step 2: Create CharacterList**

Create `frontend/src/routes/CharacterList.tsx`:

```typescript
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function CharacterList() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const location = useLocation();
  const { data, isLoading, error } = useQuery({
    queryKey: ["characters", novelId, cap],
    queryFn: () => api.characters(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <p>No characters.</p>;
  return (
    <div>
      <h1>Characters</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Aliases</th>
            <th>First seen</th>
          </tr>
        </thead>
        <tbody>
          {data.map((c) => (
            <tr key={c.id}>
              <td>
                <Link to={`/novels/${novelId}/characters/${c.id}${location.search}`}>{c.name}</Link>
              </td>
              <td>{c.aliases.join(", ") || "—"}</td>
              <td>{c.first_appearance_chapter ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Create CharacterDetail**

Create `frontend/src/routes/CharacterDetail.tsx`:

```typescript
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, CharacterDetail as Detail } from "../api";
import FieldList, { renderArray, renderJson } from "../components/FieldList";
import { useChapterCap } from "../hooks/useChapterCap";

export default function CharacterDetail() {
  const { novelId, characterId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading, error } = useQuery({
    queryKey: ["character", novelId, characterId, cap],
    queryFn: () => api.character(novelId!, characterId!, cap),
    enabled: Boolean(novelId && characterId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data) return <p>Not found.</p>;
  return <CharacterPage data={data} />;
}

function CharacterPage({ data }: { data: Detail }) {
  return (
    <article>
      <h1>{data.identity.name}</h1>
      <section>
        <h2>Identity</h2>
        <FieldList
          fields={[
            { label: "ID", value: <code>{data.identity.id}</code> },
            { label: "Aliases", value: renderArray(data.identity.aliases) },
            { label: "First appearance", value: data.identity.first_appearance_chapter ?? null },
            { label: "Description", value: data.identity.description },
          ]}
        />
      </section>
      <section>
        <h2>Current state</h2>
        {data.current_state == null ? (
          <p className="muted">No state recorded within cap.</p>
        ) : (
          <StateBlock state={data.current_state} />
        )}
      </section>
      <section>
        <h2>State history ({data.history.length})</h2>
        {data.history.map((s, i) => (
          <details key={i} open={i === data.history.length - 1}>
            <summary>Chapter {s.chapter_number}</summary>
            <StateBlock state={s} />
          </details>
        ))}
      </section>
      <section>
        <h2>Relationships ({data.relationships.length})</h2>
        {data.relationships.length === 0 ? (
          <p className="muted">None within cap.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Chapter</th>
                <th>Direction</th>
                <th>Other</th>
                <th>Type</th>
                <th>Status</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {data.relationships.map((r, i) => (
                <tr key={i}>
                  <td>{r.chapter_number ?? "—"}</td>
                  <td>{r.direction}</td>
                  <td>{r.other_character_name}</td>
                  <td>{r.rel_type ?? "—"}</td>
                  <td>{r.status ?? "—"}</td>
                  <td>{r.notes ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section>
        <h2>Events involving ({data.events.length})</h2>
        {data.events.length === 0 ? (
          <p className="muted">None within cap.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Chapter</th>
                <th>Description</th>
                <th>Type</th>
                <th>Impact</th>
                <th>With</th>
              </tr>
            </thead>
            <tbody>
              {data.events.map((e) => (
                <tr key={e.id}>
                  <td>{e.chapter_number}</td>
                  <td>{e.description}</td>
                  <td>{e.event_type ?? "—"}</td>
                  <td>{e.impact_level ?? "—"}</td>
                  <td>{e.involved_characters.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </article>
  );
}

function StateBlock({ state }: { state: Detail["history"][number] }) {
  return (
    <FieldList
      fields={[
        { label: "Chapter", value: state.chapter_number },
        { label: "Location", value: state.location },
        { label: "Emotional state", value: state.emotional_state },
        { label: "Goals", value: state.goals },
        { label: "Knowledge", value: renderArray(state.knowledge) },
        { label: "Relationships (snapshot)", value: renderJson(state.relationships) },
        { label: "Physical state", value: state.physical_state },
        { label: "Notes", value: state.notes },
      ]}
    />
  );
}
```

- [ ] **Step 4: Register routes in App.tsx**

Replace `frontend/src/App.tsx`:

```typescript
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import CharacterDetail from "./routes/CharacterDetail";
import CharacterList from "./routes/CharacterList";
import Novels from "./routes/Novels";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000 } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <Routes>
          <Route path="/" element={<Navigate to="/novels" replace />} />
          <Route path="/novels" element={<Layout><Novels /></Layout>} />
          <Route path="/novels/:novelId/characters" element={<Layout><CharacterList /></Layout>} />
          <Route path="/novels/:novelId/characters/:characterId" element={<Layout><CharacterDetail /></Layout>} />
        </Routes>
      </Router>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 5: Manual smoke test**

Start API and frontend:

```bash
uv run novel-webapp --port 8000 &
cd frontend && npm run dev
```

Visit `http://localhost:5173`, click a novel → character list → a character. Confirm:
- Sidebar shows novel title and chapter slider.
- Slider changes the URL `?cap=N` and re-fetches data.
- Character detail shows all sections (Identity, Current state, State history, Relationships, Events).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(frontend): character list + detail with all DB fields"
```

---

## Task 9: Frontend chapters, timeline, threads, continuity views

**Files:**
- Create: `frontend/src/routes/Chapters.tsx`, `Timeline.tsx`, `Threads.tsx`, `Continuity.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create `Chapters.tsx`**

```typescript
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Chapters() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading } = useQuery({
    queryKey: ["chapters", novelId, cap],
    queryFn: () => api.chapters(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  return (
    <div>
      <h1>Chapters</h1>
      <table>
        <thead>
          <tr><th>#</th><th>Title</th><th>Summary</th><th>Processed</th></tr>
        </thead>
        <tbody>
          {data?.map((c) => (
            <tr key={c.id}>
              <td>{c.number}</td>
              <td>{c.title ?? "—"}</td>
              <td>{c.summary ?? "—"}</td>
              <td>{c.processed_at ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Create `Timeline.tsx`**

```typescript
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Timeline() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading } = useQuery({
    queryKey: ["timeline", novelId, cap],
    queryFn: () => api.timeline(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (!data) return null;
  const grouped = new Map<number, typeof data>();
  for (const e of data) {
    if (!grouped.has(e.chapter_number)) grouped.set(e.chapter_number, []);
    grouped.get(e.chapter_number)!.push(e);
  }
  return (
    <div>
      <h1>Timeline</h1>
      {[...grouped.entries()].map(([chapter, events]) => (
        <section key={chapter}>
          <h2>Chapter {chapter}</h2>
          <table>
            <thead><tr><th>Description</th><th>Type</th><th>Impact</th><th>With</th></tr></thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td>{e.description}</td>
                  <td>{e.event_type ?? "—"}</td>
                  <td>{e.impact_level ?? "—"}</td>
                  <td>{e.involved_characters.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Create `Threads.tsx`**

```typescript
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Threads() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [status, setStatus] = useState<"all" | "open" | "progressing" | "closed">("all");
  const { data, isLoading } = useQuery({
    queryKey: ["threads", novelId, cap, status],
    queryFn: () => api.threads(novelId!, cap, status),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  return (
    <div>
      <h1>Threads</h1>
      <label>
        Status:{" "}
        <select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
          <option value="all">All</option>
          <option value="open">Open</option>
          <option value="progressing">Progressing</option>
          <option value="closed">Closed</option>
        </select>
      </label>
      {data?.map((t) => (
        <details key={t.id} open>
          <summary>
            <strong>{t.title}</strong> — {t.status} ({t.thread_type ?? "?"})
          </summary>
          {t.description && <p>{t.description}</p>}
          <p className="muted">
            Opened ch {t.opened_chapter ?? "—"} · Closed ch {t.closed_chapter ?? "—"}
          </p>
          <table>
            <thead><tr><th>Chapter</th><th>Event</th><th>Impact</th></tr></thead>
            <tbody>
              {t.events.map((e) => (
                <tr key={e.event_id}><td>{e.chapter_number}</td><td>{e.description}</td><td>{e.impact ?? "—"}</td></tr>
              ))}
            </tbody>
          </table>
        </details>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Create `Continuity.tsx`**

```typescript
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Continuity() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [resolved, setResolved] = useState<"all" | "open">("all");
  const { data, isLoading } = useQuery({
    queryKey: ["continuity", novelId, cap, resolved],
    queryFn: () => api.continuity(novelId!, cap, resolved),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  return (
    <div>
      <h1>Continuity flags</h1>
      <label>
        Show:{" "}
        <select value={resolved} onChange={(e) => setResolved(e.target.value as typeof resolved)}>
          <option value="all">All</option>
          <option value="open">Open only</option>
        </select>
      </label>
      <table>
        <thead><tr><th>Chapter</th><th>Description</th><th>Type</th><th>Resolved</th></tr></thead>
        <tbody>
          {data?.map((f) => (
            <tr key={f.id}>
              <td>{f.chapter_number}</td>
              <td>{f.description}</td>
              <td>{f.flag_type ?? "—"}</td>
              <td>{f.resolved ? `Yes (ch ${f.resolved_chapter_number ?? "?"})` : "No"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 5: Register routes**

Update `frontend/src/App.tsx` `<Routes>` block:

```typescript
<Route path="/novels/:novelId/chapters" element={<Layout><Chapters /></Layout>} />
<Route path="/novels/:novelId/timeline" element={<Layout><Timeline /></Layout>} />
<Route path="/novels/:novelId/threads" element={<Layout><Threads /></Layout>} />
<Route path="/novels/:novelId/continuity" element={<Layout><Continuity /></Layout>} />
```

(And add the matching imports.)

- [ ] **Step 6: Manual smoke check**

Start API + Vite, click each new sidebar link, verify each loads without errors.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(frontend): chapters, timeline, threads, continuity views"
```

---

## Task 10: Relationships graph view (vis-network)

**Files:**
- Create: `frontend/src/routes/Relationships.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create `Relationships.tsx`**

```typescript
import { useQuery } from "@tanstack/react-query";
import { DataSet } from "vis-data";
import { Network } from "vis-network/standalone/esm/vis-network";
import { useEffect, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Relationships() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["graph", novelId, cap],
    queryFn: () => api.relationships(novelId!, cap),
    enabled: Boolean(novelId),
  });

  useEffect(() => {
    if (!data || !containerRef.current) return;
    const nodes = new DataSet(
      data.nodes.map((n) => ({ id: n.id, label: n.label, title: n.description ?? undefined }))
    );
    const edges = new DataSet(
      data.edges.map((e) => ({ id: e.id, from: e.from, to: e.to, label: e.label ?? undefined, arrows: "to" }))
    );
    const network = new Network(
      containerRef.current,
      { nodes, edges },
      {
        physics: { stabilization: { iterations: 200 } },
        nodes: { shape: "dot", size: 16, font: { size: 14 } },
        edges: { font: { size: 11, align: "middle" }, smooth: { type: "continuous" } },
      }
    );
    network.on("doubleClick", (params) => {
      if (params.nodes.length > 0) {
        navigate(`/novels/${novelId}/characters/${params.nodes[0]}${window.location.search}`);
      }
    });
    networkRef.current = network;
    return () => {
      network.destroy();
      networkRef.current = null;
    };
  }, [data, navigate, novelId]);

  if (isLoading) return <p>Loading…</p>;
  return (
    <div>
      <h1>Relationships</h1>
      <p className="muted">Double-click a node to open a character. Hover for description.</p>
      <div ref={containerRef} style={{ height: 600, border: "1px solid #ddd" }} />
      <h2>All relationships ({data?.edges.length ?? 0})</h2>
      <table>
        <thead><tr><th>From</th><th>To</th><th>Type</th><th>Chapter</th></tr></thead>
        <tbody>
          {data?.edges.map((e) => {
            const fromName = data.nodes.find((n) => n.id === e.from)?.label ?? e.from;
            const toName = data.nodes.find((n) => n.id === e.to)?.label ?? e.to;
            return (
              <tr key={e.id}>
                <td>{fromName}</td>
                <td>{toName}</td>
                <td>{e.label ?? "—"}</td>
                <td>{e.chapter_number ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Register the route**

Add to `App.tsx` `<Routes>`:

```typescript
<Route path="/novels/:novelId/relationships" element={<Layout><Relationships /></Layout>} />
```

- [ ] **Step 3: Smoke test**

Visit `/novels/{id}/relationships`. Confirm:
- Graph renders with nodes labeled by character names
- Edges show relationship types
- Double-click a node navigates to that character's detail page
- Sliding the chapter cap re-renders with fewer nodes/edges

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(frontend): vis-network relationship graph"
```

---

## Task 11: Single-process production glue

**Files:**
- Modify: `backend/api/app.py`, `pyproject.toml`

- [ ] **Step 1: Update `app.py` to serve built frontend if present**

Modify `backend/api/app.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import characters, chapters, continuity, novels, relationships, threads, timeline

app = FastAPI(title="Continuum Wiki API")
app.include_router(novels.router)
app.include_router(characters.router)
app.include_router(chapters.router)
app.include_router(timeline.router)
app.include_router(threads.router)
app.include_router(continuity.router)
app.include_router(relationships.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        target = _FRONTEND_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(_FRONTEND_DIST / "index.html")


def run() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Continuum wiki web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run("api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Build the frontend and smoke-test**

```bash
cd frontend && npm run build && cd ..
uv run novel-webapp --port 8000 &
SERVER_PID=$!
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/                # → 200 (index.html)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/novels/abc      # → 200 (SPA fallback)
curl -s http://127.0.0.1:8000/api/health                                       # → {"status":"ok"}
kill $SERVER_PID
```

Expected: all three checks return 200 and the `/api/health` response.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat(api): serve built frontend in production mode"
```

---

## Task 12: Final test sweep + readme update

**Files:**
- Modify: `README.md` (briefly document the webapp)
- Run all tests

- [ ] **Step 1: Run all Python tests**

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run TypeScript type check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Update `README.md`**

Append a "Wiki Web App" section after the existing CLI sections:

```markdown
## Wiki Web App

Local read-only browser UI over the pipeline data.

**Dev (two processes):**

```bash
uv run novel-webapp --port 8000        # API on :8000
cd frontend && npm install && npm run dev   # Vite on :5173 with proxy
```

Open http://localhost:5173.

**Production-style (single process):**

```bash
cd frontend && npm run build && cd ..
uv run novel-webapp --port 8000
```

Open http://localhost:8000.

A "chapter cap" slider in the sidebar applies a global "as of chapter N" filter to every view. The Relationships page is an interactive graph (double-click a node to open the character).
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "docs: add wiki web app section to README"
```

---

## Self-review summary

**Spec coverage check:**

| Spec section | Implemented in task |
|--|--|
| Directory restructure (`backend/pipeline/` packaging trick) | Task 1 |
| API skeleton + health route | Task 2 |
| Novels list/detail | Task 3 |
| Character list/detail | Task 4 |
| Chapters/timeline/threads/continuity | Task 5 |
| Relationship graph endpoint | Task 6 |
| Frontend scaffold + sidebar + chapter cap | Task 7 |
| Frontend character views | Task 8 |
| Frontend chapter/timeline/threads/continuity views | Task 9 |
| Frontend graph view (vis-network) | Task 10 |
| Single-process production glue | Task 11 |
| Tests + docs | Threaded throughout + Task 12 |

No spec section is missing a task. No "TBD"/"TODO" placeholders. Type names (`CharacterDetail`, `RelationshipGraph`, `useChapterCap`, etc.) are consistent across tasks.
