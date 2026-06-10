# User-Defined Entity Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users define custom entity types (e.g. `realm`, `deity`, `power_system`) per novel at creation time, so the extraction pipeline classifies and stores them, and the UI lists and details them alongside the 4 hardcoded types.

**Architecture:** 4 hardcoded types (`character`, `location`, `faction`, `object`) keep their dedicated tables and rich modeling. User-defined types live only in the `entities` table — no dedicated table. A `novel_entity_types` table stores the per-novel type definitions. Genre presets (LitRPG, High Fantasy, etc.) seed a UI picker at novel-creation time. The extraction prompt is parameterised with the novel's custom types so the LLM knows to look for them.

**Tech Stack:** PostgreSQL, Python/FastAPI, Pydantic v2, TypeScript/React, TanStack Query

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/pipeline/db/schema.sql` | Add `novel_entity_types` table; remove CHECK on `entities.entity_type` |
| Create | `backend/pipeline/db/migrate_custom_entity_types.py` | Idempotent migration for existing DBs |
| Create | `backend/pipeline/extraction/presets.py` | Hardcoded genre preset dicts |
| Modify | `backend/api/schemas.py` | Add `NovelEntityType`, `CustomEntitySummary`, `CustomEntityDetail`; update `NovelCreate` |
| Modify | `backend/api/queries.py` | `create_novel` takes `custom_entity_types`; add `list_entity_types`, `list_custom_entities`, `get_custom_entity_detail` |
| Create | `backend/api/routes/entity_types.py` | `GET /api/genres`, `GET .../entity-types`, `GET .../entity-types/{type}/entities`, `GET .../custom-entities/{id}` |
| Modify | `backend/api/app.py` | Register `entity_types` router |
| Modify | `backend/api/routes/novels.py` | Pass `custom_entity_types` to `queries.create_novel` |
| Modify | `backend/api/tests/conftest.py` | Add `novel_entity_types` to FakeDB |
| Create | `backend/api/tests/test_entity_types.py` | API tests for entity-type endpoints |
| Modify | `backend/pipeline/extraction/prompts.py` | `build_system_prompt` and `build_user_prompt` accept `custom_entity_types` |
| Modify | `backend/pipeline/extraction/extractor.py` | Thread `custom_entity_types` through; handle `custom_entities` in normalize/merge |
| Modify | `backend/pipeline/pipeline.py` | Load custom types from DB in `process_chapter`; pass to extractor and context |
| Modify | `backend/pipeline/extraction/resolver.py` | `resolve_custom_entity`; handle custom types in `_resolve` / `_create_entity` / `_table_for` |
| Modify | `frontend/src/api.ts` | Add `NovelEntityType`, `CustomEntitySummary`, `CustomEntityDetail` types; API calls |
| Modify | `frontend/src/routes/Novels.tsx` | Genre picker step in create-novel form |
| Create | `frontend/src/routes/CustomEntityList.tsx` | Generic entity list page |
| Create | `frontend/src/routes/CustomEntityDetail.tsx` | Generic entity detail page (name, description, relationships) |
| Modify | `frontend/src/components/Sidebar.tsx` | Fetch entity types; append dynamic links |
| Modify | `frontend/src/App.tsx` | Add routes for custom entity list and detail |

---

## Task 1: DB Schema — `novel_entity_types` + relax CHECK

**Files:**
- Modify: `backend/pipeline/db/schema.sql`
- Create: `backend/pipeline/db/migrate_custom_entity_types.py`

- [ ] **Step 1: Add `novel_entity_types` table and drop CHECK in schema.sql**

In `schema.sql`, replace the `entities` table definition (lines 25–33):

```sql
CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    UNIQUE(novel_id, entity_type, name)
);
```

Then after the `entities` index, add:

```sql
CREATE TABLE IF NOT EXISTS novel_entity_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_novel_entity_types_novel ON novel_entity_types(novel_id);
```

- [ ] **Step 2: Write the migration file**

Create `backend/pipeline/db/migrate_custom_entity_types.py`:

```python
from __future__ import annotations

from pipeline.db.client import DBClient


def migrate() -> None:
    with DBClient() as db:
        with db.cursor(commit=True) as cur:
            # Drop the hard-coded CHECK on entity_type (if it exists).
            cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'entities_entity_type_check'
                          AND conrelid = 'entities'::regclass
                    ) THEN
                        ALTER TABLE entities DROP CONSTRAINT entities_entity_type_check;
                    END IF;
                END $$;
            """)
            # Create novel_entity_types table.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS novel_entity_types (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    description TEXT,
                    UNIQUE(novel_id, name)
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_novel_entity_types_novel
                ON novel_entity_types(novel_id);
            """)
    print("Migration complete: custom entity types schema applied.")


if __name__ == "__main__":
    migrate()
```

- [ ] **Step 3: Run the migration against a local dev DB**

```bash
cd backend && python -m pipeline.db.migrate_custom_entity_types
```

Expected: `Migration complete: custom entity types schema applied.`

- [ ] **Step 4: Commit**

```bash
git add backend/pipeline/db/schema.sql backend/pipeline/db/migrate_custom_entity_types.py
git commit -m "feat(db): add novel_entity_types table; relax entity_type CHECK constraint"
```

---

## Task 2: Genre Presets Module

**Files:**
- Create: `backend/pipeline/extraction/presets.py`

- [ ] **Step 1: Write failing test**

Create `backend/pipeline/extraction/tests/test_presets.py`:

```python
from pipeline.extraction.presets import GENRE_PRESETS, list_genres


def test_genre_presets_keys():
    assert set(GENRE_PRESETS.keys()) == {"litrpg", "high_fantasy", "xianxia", "scifi", "contemporary"}


def test_each_preset_has_name_and_description():
    for genre, types in GENRE_PRESETS.items():
        assert isinstance(types, list), genre
        for t in types:
            assert "name" in t, genre
            assert "description" in t, genre
            assert isinstance(t["name"], str) and t["name"], genre
            assert isinstance(t["description"], str) and t["description"], genre


def test_list_genres_returns_all():
    result = list_genres()
    assert len(result) == len(GENRE_PRESETS)
    keys = {r["id"] for r in result}
    assert keys == set(GENRE_PRESETS.keys())
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd backend && python -m pytest pipeline/extraction/tests/test_presets.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.extraction.presets'`

- [ ] **Step 3: Implement `presets.py`**

Create `backend/pipeline/extraction/presets.py`:

```python
from __future__ import annotations

GENRE_PRESETS: dict[str, list[dict[str, str]]] = {
    "litrpg": [
        {
            "name": "realm",
            "description": "A distinct universe, dimension, or world that characters inhabit or travel to.",
        },
        {
            "name": "power_system",
            "description": "A named system of abilities, cultivation, or magic with defined rules and tiers.",
        },
        {
            "name": "species",
            "description": "A distinct race, creature type, or non-human species with collective traits.",
        },
    ],
    "high_fantasy": [
        {
            "name": "deity",
            "description": "A god, divine being, or supernatural entity that characters worship or interact with.",
        },
        {
            "name": "species",
            "description": "A distinct race or non-human species (elves, dwarves, orcs, etc.).",
        },
        {
            "name": "magic_system",
            "description": "A named, rule-governed system of magic with distinct schools or limitations.",
        },
    ],
    "xianxia": [
        {
            "name": "realm",
            "description": "A cultivation realm, spiritual plane, or distinct world with its own power hierarchy.",
        },
        {
            "name": "cultivation_technique",
            "description": "A named cultivation method, martial art, or technique with narrative significance.",
        },
        {
            "name": "species",
            "description": "A distinct cultivator race, demon species, or supernatural being type.",
        },
    ],
    "scifi": [
        {
            "name": "species",
            "description": "An alien race or distinct non-human sapient species.",
        },
        {
            "name": "technology",
            "description": "A named technology, invention, or system with narrative significance beyond setting dressing.",
        },
    ],
    "contemporary": [
        {
            "name": "institution",
            "description": "A named institution, corporation, or organisation larger than a faction with structural importance.",
        },
    ],
}


def list_genres() -> list[dict]:
    """Return genre presets as a list of {id, label, types} for the API."""
    labels = {
        "litrpg": "LitRPG",
        "high_fantasy": "High Fantasy",
        "xianxia": "Xianxia",
        "scifi": "Sci-Fi",
        "contemporary": "Contemporary",
    }
    return [
        {"id": key, "label": labels[key], "types": types}
        for key, types in GENRE_PRESETS.items()
    ]
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
cd backend && python -m pytest pipeline/extraction/tests/test_presets.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extraction/presets.py backend/pipeline/extraction/tests/test_presets.py
git commit -m "feat(extraction): add genre presets module"
```

---

## Task 3: API — Schemas, Novel Creation with Entity Types, Genres Endpoint

**Files:**
- Modify: `backend/api/schemas.py`
- Modify: `backend/api/queries.py`
- Modify: `backend/api/routes/novels.py`
- Create: `backend/api/routes/entity_types.py`
- Modify: `backend/api/app.py`
- Modify: `backend/api/tests/conftest.py`
- Create: `backend/api/tests/test_entity_types.py`

- [ ] **Step 1: Add schemas**

In `backend/api/schemas.py`, after the `NovelCreate` class (line 23), add:

```python
class NovelEntityTypeInput(BaseModel):
    name: str
    description: str | None = None


class NovelEntityType(BaseModel):
    id: UUID
    novel_id: UUID
    name: str
    description: str | None = None


class CustomEntitySummary(BaseModel):
    id: str  # entities.id (universal_id)
    name: str
    entity_type: str
    description: str | None = None


class CustomEntityRelationship(BaseModel):
    other_entity_name: str
    other_entity_type: str
    direction: str  # "from" or "to"
    rel_type: str | None
    from_chapter: int | None
    to_chapter: int | None
    notes: str | None


class CustomEntityDetail(BaseModel):
    id: str
    name: str
    entity_type: str
    description: str | None = None
    relationships: list[CustomEntityRelationship]
```

Update `NovelCreate` to accept optional entity types:

```python
class NovelCreate(BaseModel):
    title: str
    author: str | None = None
    language: str | None = None
    custom_entity_types: list[NovelEntityTypeInput] = []
```

- [ ] **Step 2: Write failing tests**

Create `backend/api/tests/test_entity_types.py`:

```python
from __future__ import annotations

from uuid import uuid4

from api.tests.conftest import make_novel, make_chapter


def test_list_genres(fake_db_factory, client):
    fake_db_factory()
    response = client.get("/api/genres")
    assert response.status_code == 200
    body = response.json()
    ids = {g["id"] for g in body}
    assert "litrpg" in ids
    assert "high_fantasy" in ids


def test_create_novel_with_entity_types(fake_db_factory, client):
    fake_db_factory()
    response = client.post("/api/novels", json={
        "title": "My LitRPG",
        "custom_entity_types": [
            {"name": "realm", "description": "A distinct universe."},
            {"name": "power_system", "description": "Named ability system."},
        ],
    })
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "My LitRPG"


def test_create_novel_no_custom_types(fake_db_factory, client):
    fake_db_factory()
    response = client.post("/api/novels", json={"title": "Plain Novel"})
    assert response.status_code == 201


def test_list_entity_types(fake_db_factory, client):
    novel = make_novel()
    db = fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        novel_entity_types=[
            {"id": uuid4(), "novel_id": novel["id"], "name": "realm", "description": "A dimension."},
            {"id": uuid4(), "novel_id": novel["id"], "name": "power_system", "description": "Ability system."},
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/entity-types")
    assert response.status_code == 200
    body = response.json()
    names = {t["name"] for t in body}
    assert names == {"realm", "power_system"}
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd backend && python -m pytest api/tests/test_entity_types.py -v
```

Expected: 4 failures (routes and query functions don't exist yet).

- [ ] **Step 4: Add `novel_entity_types` to FakeDB**

In `backend/api/tests/conftest.py`, add `novel_entity_types` to `FakeDB.__init__`:

```python
class FakeDB:
    def __init__(
        self,
        *,
        novels: list[dict[str, Any]] | None = None,
        chapters: list[dict[str, Any]] | None = None,
        characters: list[dict[str, Any]] | None = None,
        character_states: list[dict[str, Any]] | None = None,
        events: list[dict[str, Any]] | None = None,
        relationships: list[dict[str, Any]] | None = None,
        shared_dynamics: list[dict[str, Any]] | None = None,
        plot_threads: list[dict[str, Any]] | None = None,
        thread_events: list[dict[str, Any]] | None = None,
        continuity_flags: list[dict[str, Any]] | None = None,
        locations: list[dict[str, Any]] | None = None,
        objects: list[dict[str, Any]] | None = None,
        factions: list[dict[str, Any]] | None = None,
        entities: list[dict[str, Any]] | None = None,
        novel_entity_types: list[dict[str, Any]] | None = None,
    ) -> None:
        self.novels = novels or []
        self.chapters = chapters or []
        self.characters = characters or []
        self.character_states = character_states or []
        self.events = events or []
        self.relationships = relationships or []
        self.shared_dynamics = shared_dynamics or []
        self.plot_threads = plot_threads or []
        self.thread_events = thread_events or []
        self.continuity_flags = continuity_flags or []
        self.locations = locations or []
        self.objects = objects or []
        self.factions = factions or []
        self.entities = entities or []
        self.novel_entity_types = novel_entity_types or []
```

- [ ] **Step 5: Add queries for entity types**

In `backend/api/queries.py`, update `create_novel` to also store entity types, and add `list_entity_types`:

Replace the existing `create_novel` function with:

```python
def create_novel(
    title: str,
    author: str | None,
    language: str | None,
    custom_entity_types: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
        for et in (custom_entity_types or []):
            db.novel_entity_types.append({
                "id": uuid4(),
                "novel_id": novel["id"],
                "name": et["name"],
                "description": et.get("description"),
            })
        return {**novel, "max_chapter": 0}
    return _create_novel_real(db, title, author, language, custom_entity_types or [])


def _create_novel_real(
    db: DBClient,
    title: str,
    author: str | None,
    language: str | None,
    custom_entity_types: list[dict[str, Any]],
) -> dict[str, Any]:
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
    novel_id = str(row["id"])
    for et in custom_entity_types:
        db.execute(
            """
            INSERT INTO novel_entity_types (novel_id, name, description)
            VALUES (%s, %s, %s)
            ON CONFLICT (novel_id, name) DO NOTHING
            """,
            (novel_id, et["name"], et.get("description")),
            commit=True,
        )
    return {**dict(row), "max_chapter": 0}


def list_entity_types(novel_id: UUID) -> list[dict[str, Any]]:
    db = _get_db()
    if hasattr(db, "novel_entity_types"):
        return [
            {"id": str(et["id"]), "novel_id": str(et["novel_id"]), "name": et["name"], "description": et.get("description")}
            for et in db.novel_entity_types
            if et["novel_id"] == novel_id
        ]
    rows = db.fetchall(
        "SELECT id, novel_id, name, description FROM novel_entity_types WHERE novel_id = %s ORDER BY name",
        (str(novel_id),),
        dict_rows=True,
    )
    return [dict(r) for r in rows]
```

- [ ] **Step 6: Create the entity_types route file**

Create `backend/api/routes/entity_types.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from api import queries
from api.schemas import NovelEntityType, CustomEntitySummary, CustomEntityDetail
from pipeline.extraction.presets import list_genres

router = APIRouter(tags=["entity_types"])


@router.get("/api/genres")
def get_genres() -> list[dict]:
    return list_genres()


@router.get("/api/novels/{novel_id}/entity-types", response_model=list[NovelEntityType])
def get_entity_types(novel_id: UUID) -> list[NovelEntityType]:
    rows = queries.list_entity_types(novel_id)
    return [NovelEntityType(**r) for r in rows]


@router.get(
    "/api/novels/{novel_id}/entity-types/{type_name}/entities",
    response_model=list[CustomEntitySummary],
)
def list_custom_entities(novel_id: UUID, type_name: str) -> list[CustomEntitySummary]:
    rows = queries.list_custom_entities(novel_id, type_name)
    return [CustomEntitySummary(**r) for r in rows]


@router.get(
    "/api/novels/{novel_id}/custom-entities/{entity_id}",
    response_model=CustomEntityDetail,
)
def get_custom_entity(novel_id: UUID, entity_id: UUID) -> CustomEntityDetail:
    row = queries.get_custom_entity_detail(novel_id, entity_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return CustomEntityDetail(**row)
```

- [ ] **Step 7: Update `novels.py` route to pass entity types**

In `backend/api/routes/novels.py`, update `create_novel`:

```python
from api.schemas import NovelCreate, NovelSummary


@router.post("", status_code=status.HTTP_201_CREATED, response_model=NovelSummary)
def create_novel(body: NovelCreate) -> NovelSummary:
    if not body.title or not body.title.strip():
        raise HTTPException(status_code=422, detail="title must not be blank")
    custom_types = [{"name": t.name, "description": t.description} for t in body.custom_entity_types]
    row = queries.create_novel(body.title.strip(), body.author, body.language, custom_types)
    return NovelSummary(**row)
```

- [ ] **Step 8: Register entity_types router in app.py**

In `backend/api/app.py`, add to the imports:

```python
from api.routes import (
    canon,
    chapters,
    characters,
    commitments,
    continuity,
    dynamics,
    entity_types,
    factions,
    knowledge,
    locations,
    novels,
    objects,
    process,
    relationships,
    scenes,
    threads,
    timeline,
)
```

And after the other `app.include_router(...)` calls:

```python
app.include_router(entity_types.router)
```

- [ ] **Step 9: Run tests to confirm they pass**

```bash
cd backend && python -m pytest api/tests/test_entity_types.py api/tests/test_novels.py -v
```

Expected: all tests pass (4 new + 8 existing).

- [ ] **Step 10: Commit**

```bash
git add backend/api/schemas.py backend/api/queries.py backend/api/routes/novels.py \
        backend/api/routes/entity_types.py backend/api/app.py \
        backend/api/tests/conftest.py backend/api/tests/test_entity_types.py
git commit -m "feat(api): novel creation with custom entity types; genres endpoint; entity-types list"
```

---

## Task 4: API — Custom Entity List + Detail Queries

**Files:**
- Modify: `backend/api/queries.py`
- Modify: `backend/api/tests/test_entity_types.py`

- [ ] **Step 1: Write failing tests for custom entity list and detail**

Add to `backend/api/tests/test_entity_types.py`:

```python
def test_list_custom_entities(fake_db_factory, client):
    novel = make_novel()
    entity_id = uuid4()
    db = fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        novel_entity_types=[
            {"id": uuid4(), "novel_id": novel["id"], "name": "realm", "description": "A dimension."},
        ],
        entities=[
            {"id": entity_id, "novel_id": novel["id"], "entity_type": "realm", "name": "The 93rd Universe"},
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/entity-types/realm/entities")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "The 93rd Universe"
    assert body[0]["entity_type"] == "realm"


def test_get_custom_entity_detail(fake_db_factory, client):
    novel = make_novel()
    entity_id = uuid4()
    other_entity_id = uuid4()
    db = fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        novel_entity_types=[
            {"id": uuid4(), "novel_id": novel["id"], "name": "realm", "description": "A dimension."},
        ],
        entities=[
            {"id": entity_id, "novel_id": novel["id"], "entity_type": "realm", "name": "The 93rd Universe"},
            {"id": other_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Jake"},
        ],
        relationships=[
            {
                "id": uuid4(),
                "entity_a_id": other_entity_id,
                "entity_b_id": entity_id,
                "rel_type": "inhabits",
                "from_chapter": 1,
                "to_chapter": None,
                "notes": None,
            }
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/custom-entities/{entity_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "The 93rd Universe"
    assert body["entity_type"] == "realm"
    assert len(body["relationships"]) == 1
    assert body["relationships"][0]["rel_type"] == "inhabits"


def test_get_custom_entity_detail_404(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[], entities=[])
    response = client.get(f"/api/novels/{novel['id']}/custom-entities/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 404
```

- [ ] **Step 2: Run failing tests**

```bash
cd backend && python -m pytest api/tests/test_entity_types.py::test_list_custom_entities api/tests/test_entity_types.py::test_get_custom_entity_detail api/tests/test_entity_types.py::test_get_custom_entity_detail_404 -v
```

Expected: failures because `list_custom_entities` and `get_custom_entity_detail` don't exist in queries.py.

- [ ] **Step 3: Add `list_custom_entities` and `get_custom_entity_detail` to queries.py**

Add to `backend/api/queries.py`:

```python
def list_custom_entities(novel_id: UUID, entity_type: str) -> list[dict[str, Any]]:
    db = _get_db()
    if hasattr(db, "entities"):
        return [
            {
                "id": str(e["id"]),
                "name": e["name"],
                "entity_type": e["entity_type"],
                "description": e.get("description"),
            }
            for e in db.entities
            if e.get("novel_id") == novel_id and e.get("entity_type") == entity_type
        ]
    rows = db.fetchall(
        """
        SELECT id, name, entity_type, NULL AS description
        FROM entities
        WHERE novel_id = %s AND entity_type = %s
        ORDER BY name
        """,
        (str(novel_id), entity_type),
        dict_rows=True,
    )
    return [
        {"id": str(r["id"]), "name": r["name"], "entity_type": r["entity_type"], "description": r.get("description")}
        for r in rows
    ]


def get_custom_entity_detail(novel_id: UUID, entity_id: UUID) -> dict[str, Any] | None:
    db = _get_db()
    if hasattr(db, "entities"):
        entity = next(
            (e for e in db.entities if e["id"] == entity_id and e.get("novel_id") == novel_id),
            None,
        )
        if entity is None:
            return None
        entity_id_str = str(entity_id)
        rels = [
            r for r in db.relationships
            if str(r["entity_a_id"]) == entity_id_str or str(r["entity_b_id"]) == entity_id_str
        ]
        entity_by_id = {str(e["id"]): e for e in db.entities}
        relationships = []
        for r in rels:
            if str(r["entity_a_id"]) == entity_id_str:
                other_id = str(r["entity_b_id"])
                direction = "from"
            else:
                other_id = str(r["entity_a_id"])
                direction = "to"
            other = entity_by_id.get(other_id, {})
            relationships.append({
                "other_entity_name": other.get("name", other_id),
                "other_entity_type": other.get("entity_type", "unknown"),
                "direction": direction,
                "rel_type": r.get("rel_type"),
                "from_chapter": r.get("from_chapter"),
                "to_chapter": r.get("to_chapter"),
                "notes": r.get("notes"),
            })
        return {
            "id": entity_id_str,
            "name": entity["name"],
            "entity_type": entity["entity_type"],
            "description": entity.get("description"),
            "relationships": relationships,
        }
    row = db.fetchone(
        "SELECT id, name, entity_type FROM entities WHERE id = %s AND novel_id = %s",
        (str(entity_id), str(novel_id)),
        dict_rows=True,
    )
    if row is None:
        return None
    rels_rows = db.fetchall(
        """
        SELECT r.entity_a_id, r.entity_b_id, r.rel_type, r.from_chapter, r.to_chapter, r.notes,
               ea.name AS name_a, ea.entity_type AS type_a,
               eb.name AS name_b, eb.entity_type AS type_b
        FROM relationships r
        JOIN entities ea ON ea.id = r.entity_a_id
        JOIN entities eb ON eb.id = r.entity_b_id
        WHERE r.entity_a_id = %s OR r.entity_b_id = %s
        """,
        (str(entity_id), str(entity_id)),
        dict_rows=True,
    )
    relationships = []
    for r in rels_rows:
        if str(r["entity_a_id"]) == str(entity_id):
            other_name, other_type, direction = r["name_b"], r["type_b"], "from"
        else:
            other_name, other_type, direction = r["name_a"], r["type_a"], "to"
        relationships.append({
            "other_entity_name": other_name,
            "other_entity_type": other_type,
            "direction": direction,
            "rel_type": r.get("rel_type"),
            "from_chapter": r.get("from_chapter"),
            "to_chapter": r.get("to_chapter"),
            "notes": r.get("notes"),
        })
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "entity_type": row["entity_type"],
        "description": None,
        "relationships": relationships,
    }
```

- [ ] **Step 4: Run all entity_types tests**

```bash
cd backend && python -m pytest api/tests/test_entity_types.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/api/queries.py backend/api/tests/test_entity_types.py
git commit -m "feat(api): custom entity list and detail endpoints"
```

---

## Task 5: Pipeline — Dynamic Extraction Prompts

**Files:**
- Modify: `backend/pipeline/extraction/prompts.py`
- Modify: `backend/pipeline/extraction/extractor.py`
- Modify: `backend/pipeline/pipeline.py`

- [ ] **Step 1: Write failing tests**

Create `backend/pipeline/extraction/tests/test_custom_entity_extraction.py`:

```python
from pipeline.extraction.prompts import build_system_prompt, build_user_prompt
from pipeline.extraction.extractor import empty_extraction, _normalize_extraction, merge_extractions


def test_build_system_prompt_no_custom_types():
    prompt = build_system_prompt("new_entities")
    assert "custom_entities" not in prompt


def test_build_system_prompt_with_custom_types():
    custom_types = [
        {"name": "realm", "description": "A distinct universe."},
        {"name": "power_system", "description": "Named ability system."},
    ]
    prompt = build_system_prompt("new_entities", custom_entity_types=custom_types)
    assert "custom_entities" in prompt
    assert "realm" in prompt
    assert "power_system" in prompt


def test_normalize_extraction_custom_entities():
    raw = {
        "custom_entities": [
            {"name": "The 93rd Universe", "type": "realm", "description": "A dimension."},
        ]
    }
    result = _normalize_extraction(raw)
    assert result["custom_entities"] == [
        {"name": "The 93rd Universe", "type": "realm", "description": "A dimension."}
    ]


def test_empty_extraction_has_custom_entities():
    result = empty_extraction()
    assert "custom_entities" in result
    assert result["custom_entities"] == []


def test_merge_extractions_custom_entities():
    e1 = empty_extraction()
    e1["custom_entities"] = [{"name": "The 93rd Universe", "type": "realm", "description": "A dimension."}]
    e2 = empty_extraction()
    e2["custom_entities"] = [{"name": "The System", "type": "power_system", "description": "Ability system."}]
    merged = merge_extractions([e1, e2])
    names = {e["name"] for e in merged["custom_entities"]}
    assert names == {"The 93rd Universe", "The System"}


def test_merge_extractions_deduplicates_custom_entities():
    e1 = empty_extraction()
    e1["custom_entities"] = [{"name": "The 93rd Universe", "type": "realm", "description": "A dimension."}]
    e2 = empty_extraction()
    e2["custom_entities"] = [{"name": "The 93rd Universe", "type": "realm", "description": "Same thing."}]
    merged = merge_extractions([e1, e2])
    assert len(merged["custom_entities"]) == 1
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd backend && python -m pytest pipeline/extraction/tests/test_custom_entity_extraction.py -v
```

Expected: failures (functions don't accept `custom_entity_types` param yet; `custom_entities` key absent).

- [ ] **Step 3: Update `build_system_prompt` in prompts.py**

In `backend/pipeline/extraction/prompts.py`, replace `build_system_prompt`:

```python
def build_system_prompt(pass_name: str, custom_entity_types: list[dict] | None = None) -> str:
    schema = PASS_SCHEMAS[pass_name]
    if pass_name == "new_entities" and custom_entity_types:
        schema = dict(schema)
        schema["custom_entities"] = [
            {
                "name": "string",
                "type": " | ".join(t["name"] for t in custom_entity_types),
                "description": "string",
            }
        ]
    return dedent(
        f"""
        You are an extraction engine for a novel continuity pipeline.
        Return only strict JSON. No prose, no markdown.
        Keep facts grounded in provided text.

        Extraction pass: {pass_name}
        Required output schema:
        {json.dumps(schema, ensure_ascii=True, indent=2)}
        """
    ).strip()
```

- [ ] **Step 4: Update `build_user_prompt` in prompts.py**

Replace `build_user_prompt`:

```python
def build_user_prompt(pass_name: str, chunk: str, context: dict, custom_entity_types: list[dict] | None = None) -> str:
    context_block = build_context_block(context)
    task = PASS_TASK_INSTRUCTIONS.get(
        pass_name, f"Execute the {pass_name} pass and return JSON only."
    )
    custom_block = ""
    if pass_name == "new_entities" and custom_entity_types:
        type_lines = "\n".join(
            f"  - {t['name']}: {t.get('description', '')}"
            for t in custom_entity_types
        )
        existing_custom = context.get("custom_entities", {})
        existing_lines = ""
        for type_name, items in existing_custom.items():
            if items:
                names = ", ".join(i["name"] for i in items)
                existing_lines += f"\n  {type_name} (already known): {names}"
        custom_block = dedent(f"""
        CUSTOM ENTITY TYPES FOR THIS NOVEL
        Extract entities of these types into the custom_entities array:
        {type_lines}

        Only extract if genuinely new and not already in STORY CONTEXT.
        Each item: {{name, type (one of the types above), description}}.
        {existing_lines}
        """).strip()
    return dedent(
        f"""
        {context_block}

        CHAPTER CHUNK
        {chunk}

        TASK
        {task}
        {custom_block}
        Return JSON only.
        """
    ).strip()
```

- [ ] **Step 5: Update `empty_extraction` in extractor.py**

Add `"custom_entities": []` to the dict returned by `empty_extraction()`:

```python
def empty_extraction() -> dict[str, Any]:
    return {
        "summary": "",
        "new_entities": {
            "characters": [],
            "locations": [],
            "factions": [],
            "objects": [],
        },
        "custom_entities": [],
        "entity_deltas": [],
        "events": [],
        "thread_updates": [],
        "continuity_flags": [],
        "relationship_updates": [],
        "dynamics_updates": [],
        "scenes": [],
        "summary_short": "",
        "summary_medium": "",
        "summary_long": "",
        "learnings": [],
        "foreshadows_introduced": [],
        "payoffs_delivered": [],
    }
```

- [ ] **Step 6: Update `_normalize_extraction` in extractor.py**

After the `payoffs` block near line 148, add:

```python
    custom_entities = raw.get("custom_entities", [])
    if isinstance(custom_entities, list):
        output["custom_entities"] = [
            item for item in custom_entities
            if isinstance(item, dict) and item.get("name") and item.get("type")
        ]
```

- [ ] **Step 7: Update `merge_extractions` in extractor.py**

After the payoffs merge block (near line 334), add:

```python
    # Custom entities: dedupe by (name.lower(), type).
    seen_custom: set[tuple[str, str]] = set()
    for extraction in extractions:
        for ce in extraction.get("custom_entities", []):
            if not isinstance(ce, dict):
                continue
            key = (str(ce.get("name", "")).strip().lower(), str(ce.get("type", "")).strip().lower())
            if not key[0] or not key[1] or key in seen_custom:
                continue
            seen_custom.add(key)
            merged["custom_entities"].append(ce)
```

- [ ] **Step 8: Thread `custom_entity_types` through `ChapterExtractor`**

In `extractor.py`, update `ChapterExtractor.extract_chapter`:

```python
    def extract_chapter(
        self,
        chunks: list[str],
        context: dict[str, Any],
        progress: Any | None = None,
        custom_entity_types: list[dict] | None = None,
    ) -> dict[str, Any]:
        if not chunks:
            return empty_extraction()
        results = [
            self.extract_chunk(chunk, context, progress=progress, custom_entity_types=custom_entity_types)
            for chunk in chunks
        ]
        return merge_extractions(results)
```

Update `extract_chunk`:

```python
    def extract_chunk(
        self,
        chunk: str,
        context: dict[str, Any],
        progress: Any | None = None,
        custom_entity_types: list[dict] | None = None,
    ) -> dict[str, Any]:
        if self.use_mock:
            return self._mock_extract(chunk, context)

        pass_payload: dict[str, Any] = {}
        for pass_name in PASS_ORDER:
            if progress is not None:
                progress.on_pass_start(pass_name)
            payload = self._run_llm_pass(pass_name, chunk, context, custom_entity_types=custom_entity_types)
            if progress is not None:
                progress.on_pass_done(pass_name)
            pass_payload[pass_name] = payload

        normalized = self._compose_from_pass_payload(pass_payload)
        return _normalize_extraction(normalized)
```

Update `_run_llm_pass`:

```python
    def _run_llm_pass(
        self,
        pass_name: str,
        chunk: str,
        context: dict[str, Any],
        custom_entity_types: list[dict] | None = None,
    ) -> dict[str, Any]:
        completion = _load_completion()
        if completion is None:
            return {}

        system_prompt = build_system_prompt(pass_name, custom_entity_types=custom_entity_types)
        user_prompt = build_user_prompt(pass_name, chunk, context, custom_entity_types=custom_entity_types)

        try:
            response = completion(
                model=LLM_CONFIG["model"],
                temperature=LLM_CONFIG["temperature"],
                response_format=LLM_CONFIG["response_format"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content
            if isinstance(content, list):
                content = "".join(str(part) for part in content)
            payload = _safe_json_loads(str(content))
            return payload
        except Exception as exc:  # pragma: no cover
            logger.warning("LLM pass failed (%s): %s", pass_name, exc)
            return {}
```

Also update `_compose_from_pass_payload` to forward `custom_entities` from the `new_entities` pass payload:

```python
    def _compose_from_pass_payload(self, pass_payload: dict[str, dict[str, Any]]) -> dict[str, Any]:
        # ... existing code ...
        new_entities = pass_payload.get("new_entities", {})
        # ... rest of assignments ...
        return {
            "summary": chapter_summary.get("summary", ""),
            "new_entities": new_entities,
            "custom_entities": new_entities.get("custom_entities", []),  # hoisted out of new_entities
            "entity_deltas": entity_deltas.get("character_deltas", entity_deltas.get("entity_deltas", [])),
            # ... rest unchanged ...
        }
```

- [ ] **Step 9: Load custom types in `pipeline.py` `process_chapter`**

In `backend/pipeline/pipeline.py`, inside `process_chapter`, after the `ingest_chapter` call, add:

```python
        custom_entity_types = [
            dict(r)
            for r in db.fetchall(
                "SELECT name, description FROM novel_entity_types WHERE novel_id = %s ORDER BY name",
                (novel_id,),
                dict_rows=True,
            )
        ]
```

Then update the extractor call to pass custom types:

```python
        extracted = extractor.extract_chapter(
            chunks=chunks,
            context=context,
            progress=progress,
            custom_entity_types=custom_entity_types or None,
        )
```

Update `load_story_context` to include custom entities already extracted. In `pipeline.py`, update `load_story_context` signature to accept `custom_entity_types` and add a query block:

```python
def load_story_context(
    db: DBClient,
    novel_id: str,
    chapter_number: int,
    custom_entity_types: list[dict] | None = None,
) -> dict[str, Any]:
    # ... existing queries for characters, locations, threads, recent_events ...

    custom_entities: dict[str, list[dict]] = {}
    for et in (custom_entity_types or []):
        type_name = et["name"]
        rows = db.fetchall(
            "SELECT name FROM entities WHERE novel_id = %s AND entity_type = %s ORDER BY name",
            (novel_id, type_name),
            dict_rows=True,
        )
        custom_entities[type_name] = [{"name": r["name"]} for r in rows]

    return {
        "characters": [dict(row) for row in characters],
        "locations": [dict(row) for row in locations],
        "open_threads": [dict(row) for row in open_threads],
        "recent_events": [dict(row) for row in recent_events],
        "custom_entities": custom_entities,
    }
```

In `process_chapter`, update the `load_story_context` call:

```python
        context = load_story_context(db, novel_id, chapter_number, custom_entity_types=custom_entity_types)
```

- [ ] **Step 10: Run all extraction tests**

```bash
cd backend && python -m pytest pipeline/extraction/tests/test_custom_entity_extraction.py pipeline/extraction/tests/test_extractor.py -v
```

Expected: all tests pass.

- [ ] **Step 11: Commit**

```bash
git add backend/pipeline/extraction/prompts.py backend/pipeline/extraction/extractor.py \
        backend/pipeline/pipeline.py \
        backend/pipeline/extraction/tests/test_custom_entity_extraction.py
git commit -m "feat(pipeline): dynamic extraction prompt and normalization for custom entity types"
```

---

## Task 6: Pipeline — Resolver Handles Custom Entity Types

**Files:**
- Modify: `backend/pipeline/extraction/resolver.py`
- Modify: `backend/pipeline/pipeline.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/pipeline/extraction/tests/test_resolver.py` (or create it if it only has stubs):

```python
from unittest.mock import MagicMock, patch
from pipeline.extraction.resolver import EntityResolver, ResolvedEntity


def _make_db(entities=None):
    db = MagicMock()
    db.entities = entities or []
    db.fetchone = MagicMock(return_value=None)
    db.fetchval = MagicMock(return_value="new-uuid-123")
    db.execute = MagicMock()
    return db


def test_resolve_custom_entity_creates_in_entities_only():
    db = _make_db()
    db.fetchval.return_value = "realm-uuid"
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    result = resolver.resolve_custom_entity("The 93rd Universe", "realm", {"description": "A dimension."})
    assert isinstance(result, ResolvedEntity)
    # entity_id == universal_id for custom types (no dedicated table)
    assert result.entity_id == result.universal_id
    assert result.created is True
    # Only inserted into entities, NOT into characters/locations/etc.
    db.fetchval.assert_called_once()
    call_sql = db.fetchval.call_args[0][0]
    assert "entities" in call_sql
    assert "characters" not in call_sql


def test_resolve_custom_entity_cache_hit():
    db = _make_db()
    db.fetchval.return_value = "realm-uuid"
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    r1 = resolver.resolve_custom_entity("The 93rd Universe", "realm", {})
    r2 = resolver.resolve_custom_entity("The 93rd Universe", "realm", {})
    assert r1.entity_id == r2.entity_id
    assert db.fetchval.call_count == 1  # only created once
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd backend && python -m pytest pipeline/extraction/tests/test_resolver.py::test_resolve_custom_entity_creates_in_entities_only pipeline/extraction/tests/test_resolver.py::test_resolve_custom_entity_cache_hit -v
```

Expected: `AttributeError: EntityResolver has no attribute 'resolve_custom_entity'`.

- [ ] **Step 3: Update `resolver.py`**

In `backend/pipeline/extraction/resolver.py`, add `resolve_custom_entity` method to `EntityResolver`:

```python
    def resolve_custom_entity(
        self, name: str, entity_type: str, metadata: dict[str, Any] | None = None
    ) -> ResolvedEntity:
        """Resolve or create a custom-typed entity (entities table only, no dedicated table)."""
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError(f"Cannot resolve empty {entity_type} name")

        cache_key = (entity_type, normalized_name.lower())
        cached = self._cache.get(cache_key)
        if cached:
            return ResolvedEntity(cached[0], cached[1], created=False)

        # Check existing entity in entities table directly.
        if hasattr(self.db, "entities"):
            entity = next(
                (
                    e for e in self.db.entities
                    if str(e.get("novel_id")) == str(self.novel_id)
                    and str(e.get("entity_type")) == entity_type
                    and str(e.get("name", "")).lower() == normalized_name.lower()
                ),
                None,
            )
            if entity:
                uid = str(entity["id"])
                self._cache[cache_key] = (uid, uid)
                return ResolvedEntity(uid, uid, created=False)
        else:
            row = self.db.fetchone(
                """
                SELECT id FROM entities
                WHERE novel_id = %s AND entity_type = %s AND lower(name) = lower(%s)
                LIMIT 1
                """,
                (self.novel_id, entity_type, normalized_name),
            )
            if row:
                uid = str(row[0])
                self._cache[cache_key] = (uid, uid)
                return ResolvedEntity(uid, uid, created=False)

        # Create: insert into entities only.
        universal_id = str(self.db.fetchval(
            """
            INSERT INTO entities (novel_id, entity_type, name)
            VALUES (%s, %s, %s)
            ON CONFLICT (novel_id, entity_type, name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (self.novel_id, entity_type, normalized_name),
            commit=True,
        ))
        self._cache[cache_key] = (universal_id, universal_id)
        return ResolvedEntity(universal_id, universal_id, created=True)
```

- [ ] **Step 4: Persist custom entities in `_persist_extraction` in `pipeline.py`**

In `backend/pipeline/pipeline.py`, inside `_persist_extraction`, after the block that processes `objects`, add:

```python
    custom_entity_types = {
        et["name"]
        for et in db.fetchall(
            "SELECT name FROM novel_entity_types WHERE novel_id = %s",
            (resolver.novel_id,),
            dict_rows=True,
        )
    } if not hasattr(db, "novel_entity_types") else set()

    for custom_entity in extracted.get("custom_entities", []):
        name = str(custom_entity.get("name", "")).strip()
        entity_type = str(custom_entity.get("type", "")).strip()
        if not name or not entity_type:
            continue
        resolver.resolve_custom_entity(name, entity_type, custom_entity)
```

- [ ] **Step 5: Run resolver tests**

```bash
cd backend && python -m pytest pipeline/extraction/tests/test_resolver.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/extraction/resolver.py backend/pipeline/pipeline.py \
        backend/pipeline/extraction/tests/test_resolver.py
git commit -m "feat(pipeline): resolver and persistence for custom entity types"
```

---

## Task 7: Frontend — API Types + Calls

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add types and API calls to `api.ts`**

Add the following types to `frontend/src/api.ts` after the existing type definitions:

```typescript
export type NovelEntityType = {
  id: string;
  novel_id: string;
  name: string;
  description: string | null;
};

export type GenrePreset = {
  id: string;
  label: string;
  types: { name: string; description: string }[];
};

export type CustomEntitySummary = {
  id: string;
  name: string;
  entity_type: string;
  description: string | null;
};

export type CustomEntityRelationship = {
  other_entity_name: string;
  other_entity_type: string;
  direction: "from" | "to";
  rel_type: string | null;
  from_chapter: number | null;
  to_chapter: number | null;
  notes: string | null;
};

export type CustomEntityDetail = {
  id: string;
  name: string;
  entity_type: string;
  description: string | null;
  relationships: CustomEntityRelationship[];
};
```

Add to the `api` object:

```typescript
  genres: () => fetchJson<GenrePreset[]>("/api/genres"),
  entityTypes: (novelId: string) =>
    fetchJson<NovelEntityType[]>(`/api/novels/${novelId}/entity-types`),
  customEntities: (novelId: string, typeName: string) =>
    fetchJson<CustomEntitySummary[]>(`/api/novels/${novelId}/entity-types/${typeName}/entities`),
  customEntity: (novelId: string, entityId: string) =>
    fetchJson<CustomEntityDetail>(`/api/novels/${novelId}/custom-entities/${entityId}`),
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(frontend): add custom entity types and API calls to api.ts"
```

---

## Task 8: Frontend — Novel Creation with Genre Picker

**Files:**
- Modify: `frontend/src/routes/Novels.tsx`

- [ ] **Step 1: Rewrite `Novels.tsx` with a two-step create form**

Replace the entire content of `frontend/src/routes/Novels.tsx`:

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Novel, GenrePreset } from "../api";

type EntityTypeInput = { name: string; description: string };

async function createNovel(
  title: string,
  author: string,
  language: string,
  customEntityTypes: EntityTypeInput[]
): Promise<Novel> {
  const res = await fetch("/api/novels", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      author: author.trim() || null,
      language: language.trim() || null,
      custom_entity_types: customEntityTypes,
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
  const [step, setStep] = useState<"details" | "types">("details");
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [language, setLanguage] = useState("");
  const [customTypes, setCustomTypes] = useState<EntityTypeInput[]>([]);
  const [newTypeName, setNewTypeName] = useState("");

  const { data, isLoading, error } = useQuery({ queryKey: ["novels"], queryFn: api.novels });
  const { data: genres } = useQuery({ queryKey: ["genres"], queryFn: api.genres });

  const mutation = useMutation({
    mutationFn: () => createNovel(title, author, language, customTypes),
    onSuccess: (novel) => {
      queryClient.invalidateQueries({ queryKey: ["novels"] });
      navigate(`/novels/${novel.id}/process`);
    },
  });

  function applyPreset(preset: GenrePreset) {
    const existing = new Set(customTypes.map((t) => t.name));
    const toAdd = preset.types.filter((t) => !existing.has(t.name));
    setCustomTypes((prev) => [...prev, ...toAdd]);
  }

  function removeType(name: string) {
    setCustomTypes((prev) => prev.filter((t) => t.name !== name));
  }

  function addCustomType() {
    const trimmed = newTypeName.trim().toLowerCase().replace(/\s+/g, "_");
    if (!trimmed || customTypes.some((t) => t.name === trimmed)) return;
    setCustomTypes((prev) => [...prev, { name: trimmed, description: "" }]);
    setNewTypeName("");
  }

  function resetForm() {
    setTitle(""); setAuthor(""); setLanguage("");
    setCustomTypes([]); setNewTypeName("");
    setStep("details"); setShowForm(false);
  }

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;

  return (
    <div>
      <h1>Novels</h1>

      {!showForm && (
        <button onClick={() => setShowForm(true)}>New Novel</button>
      )}

      {showForm && step === "details" && (
        <form onSubmit={(e) => { e.preventDefault(); if (!title.trim()) return; setStep("types"); }} style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-title">Title</label><br />
            <input id="novel-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} required style={{ marginTop: 4 }} />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-author">Author (optional)</label><br />
            <input id="novel-author" type="text" value={author} onChange={(e) => setAuthor(e.target.value)} style={{ marginTop: 4 }} />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-language">Language (optional)</label><br />
            <input id="novel-language" type="text" value={language} onChange={(e) => setLanguage(e.target.value)} style={{ marginTop: 4 }} />
          </div>
          <button type="submit" disabled={!title.trim()}>Next: Entity Types →</button>
          {" "}
          <button type="button" onClick={resetForm}>Cancel</button>
        </form>
      )}

      {showForm && step === "types" && (
        <div style={{ marginBottom: 16 }}>
          <h3>Custom Entity Types (optional)</h3>
          <p style={{ color: "#666", fontSize: 14 }}>
            Define types beyond Characters, Locations, Factions, and Objects.
          </p>

          {genres && genres.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <strong>Genre presets:</strong>
              {" "}
              {genres.map((g) => (
                <button key={g.id} type="button" onClick={() => applyPreset(g)} style={{ marginRight: 6, marginBottom: 4 }}>
                  {g.label}
                </button>
              ))}
            </div>
          )}

          {customTypes.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <strong>Selected types:</strong>
              <ul style={{ margin: "4px 0", paddingLeft: 20 }}>
                {customTypes.map((t) => (
                  <li key={t.name}>
                    <code>{t.name}</code>
                    {t.description && <span style={{ color: "#666", fontSize: 13 }}> — {t.description}</span>}
                    {" "}
                    <button type="button" onClick={() => removeType(t.name)} style={{ fontSize: 11 }}>✕</button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div style={{ marginBottom: 12 }}>
            <input
              type="text"
              placeholder="Add custom type (e.g. deity)"
              value={newTypeName}
              onChange={(e) => setNewTypeName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustomType(); } }}
            />
            {" "}
            <button type="button" onClick={addCustomType}>Add</button>
          </div>

          {mutation.isError && (
            <p style={{ color: "red" }}>Error: {(mutation.error as Error).message}</p>
          )}
          <button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending ? "Creating…" : "Create Novel"}
          </button>
          {" "}
          <button type="button" onClick={() => setStep("details")} disabled={mutation.isPending}>← Back</button>
          {" "}
          <button type="button" onClick={resetForm} disabled={mutation.isPending}>Cancel</button>
        </div>
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

- [ ] **Step 2: Start the dev server and test manually**

```bash
cd frontend && npm run dev
```

Open the app, click "New Novel", fill in title, click "Next: Entity Types →", verify genre presets appear, select one, verify types appear with remove buttons, click "Create Novel".

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/Novels.tsx
git commit -m "feat(frontend): two-step novel creation with genre picker and custom entity types"
```

---

## Task 9: Frontend — Custom Entity Pages, Sidebar, Routing

**Files:**
- Create: `frontend/src/routes/CustomEntityList.tsx`
- Create: `frontend/src/routes/CustomEntityDetail.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create `CustomEntityList.tsx`**

Create `frontend/src/routes/CustomEntityList.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function CustomEntityList() {
  const { novelId, typeName } = useParams<{ novelId: string; typeName: string }>();
  const [cap] = useChapterCap();

  const { data, isLoading, error } = useQuery({
    queryKey: ["custom-entities", novelId, typeName],
    queryFn: () => api.customEntities(novelId!, typeName!),
    enabled: Boolean(novelId && typeName),
  });

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;

  const label = typeName ? typeName.replace(/_/g, " ") : "";
  const displayLabel = label.charAt(0).toUpperCase() + label.slice(1) + "s";

  return (
    <div>
      <h1>{displayLabel}</h1>
      {(!data || data.length === 0) && <p>None found.</p>}
      {data && data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {data.map((e) => (
              <tr key={e.id}>
                <td>
                  <Link to={`/novels/${novelId}/custom-entities/${e.id}`}>{e.name}</Link>
                </td>
                <td>{e.description ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `CustomEntityDetail.tsx`**

Create `frontend/src/routes/CustomEntityDetail.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { FieldList } from "../components/FieldList";

export default function CustomEntityDetail() {
  const { novelId, entityId } = useParams<{ novelId: string; entityId: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["custom-entity", novelId, entityId],
    queryFn: () => api.customEntity(novelId!, entityId!),
    enabled: Boolean(novelId && entityId),
  });

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data) return <p>Not found.</p>;

  const typeLabel = data.entity_type.replace(/_/g, " ");

  return (
    <div>
      <h1>{data.name}</h1>
      <p style={{ color: "#666", textTransform: "capitalize" }}>{typeLabel}</p>
      {data.description && <p>{data.description}</p>}

      {data.relationships.length > 0 && (
        <>
          <h2>Relationships</h2>
          <table>
            <thead>
              <tr>
                <th>Entity</th>
                <th>Type</th>
                <th>Relation</th>
                <th>Direction</th>
                <th>From Ch.</th>
              </tr>
            </thead>
            <tbody>
              {data.relationships.map((r, i) => (
                <tr key={i}>
                  <td>{r.other_entity_name}</td>
                  <td style={{ textTransform: "capitalize" }}>{r.other_entity_type.replace(/_/g, " ")}</td>
                  <td>{r.rel_type ?? "—"}</td>
                  <td>{r.direction === "from" ? "→" : "←"}</td>
                  <td>{r.from_chapter ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Update `Sidebar.tsx` to show custom entity type links**

Replace the entire `Sidebar.tsx` with:

```tsx
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

  const entityTypesQuery = useQuery({
    queryKey: ["entity-types", novelId],
    queryFn: () => api.entityTypes(novelId!),
    enabled: Boolean(novelId),
  });

  const max = novelQuery.data?.max_chapter ?? null;
  const effective = cap ?? max ?? 0;

  const staticLinks: [string, string][] = novelId
    ? [
        ["Characters", `/novels/${novelId}/characters`],
        ["Chapters", `/novels/${novelId}/chapters`],
        ["Scenes", `/novels/${novelId}/scenes`],
        ["Timeline", `/novels/${novelId}/timeline`],
        ["Threads", `/novels/${novelId}/threads`],
        ["Commitments", `/novels/${novelId}/commitments`],
        ["State & Knowledge", `/novels/${novelId}/knowledge`],
        ["Canon Facts", `/novels/${novelId}/canon`],
        ["Locations", `/novels/${novelId}/locations`],
        ["Objects", `/novels/${novelId}/objects`],
        ["Factions", `/novels/${novelId}/factions`],
        ["Dynamics", `/novels/${novelId}/dynamics`],
        ["Continuity", `/novels/${novelId}/continuity`],
        ["Process Chapter", `/novels/${novelId}/process`],
      ]
    : [];

  const customLinks: [string, string][] = (entityTypesQuery.data ?? []).map((et) => {
    const label = et.name.replace(/_/g, " ");
    const displayLabel = label.charAt(0).toUpperCase() + label.slice(1) + "s";
    return [displayLabel, `/novels/${novelId}/entity-types/${et.name}/entities`];
  });

  const allLinks = [...staticLinks, ...customLinks];

  return (
    <aside className="sidebar">
      <h1>
        <Link to="/novels">Continuum</Link>
      </h1>
      {novelQuery.data && <h2>{novelQuery.data.title}</h2>}
      <nav>
        <ul>
          {allLinks.map(([label, to]) => (
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

- [ ] **Step 4: Update `App.tsx` with new routes**

In `frontend/src/App.tsx`, add imports:

```tsx
import CustomEntityList from "./routes/CustomEntityList";
import CustomEntityDetail from "./routes/CustomEntityDetail";
```

Add routes inside `<Routes>`:

```tsx
<Route path="/novels/:novelId/entity-types/:typeName/entities" element={<Layout><CustomEntityList /></Layout>} />
<Route path="/novels/:novelId/custom-entities/:entityId" element={<Layout><CustomEntityDetail /></Layout>} />
```

- [ ] **Step 5: Build and test**

```bash
cd frontend && npm run build
```

Expected: no TypeScript errors.

Start the API and dev server, navigate to a novel with custom entity types, verify the sidebar shows the custom type links, click one, verify the list loads.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/CustomEntityList.tsx frontend/src/routes/CustomEntityDetail.tsx \
        frontend/src/components/Sidebar.tsx frontend/src/App.tsx
git commit -m "feat(frontend): custom entity list/detail pages, sidebar dynamic links, new routes"
```

---

## Task 10: Update Docs Website

Per project instructions, update the docs website after every change.

- [ ] **Step 1: Build frontend**

```bash
cd frontend && npm run build
```

- [ ] **Step 2: Commit docs if there's a separate docs build step**

```bash
git add -A && git commit -m "chore: update docs website for custom entity types feature"
```

---

## Self-Review

### Spec Coverage

| Requirement | Task |
|-------------|------|
| 4 hardcoded types keep their dedicated tables | Schema unchanged for characters/locations/factions/objects |
| User-defined types live only in `entities` | Task 6 resolver creates entities only |
| `novel_entity_types` table stores definitions | Task 1 schema, Task 3 queries |
| Types defined at novel creation time | Task 3 NovelCreate + route |
| Genre presets (LitRPG, High Fantasy, Xianxia, Sci-Fi, Contemporary) | Task 2 presets.py |
| Extraction uses user's types | Task 5 prompts + extractor |
| Custom entities appear in context so LLM knows what's already extracted | Task 5 load_story_context |
| API: list entity types | Task 3 |
| API: list custom entities by type | Task 4 |
| API: custom entity detail with relationships | Task 4 |
| UI: genre picker at novel creation | Task 8 |
| UI: sidebar links for custom types | Task 9 |
| UI: custom entity list + detail pages | Task 9 |

### Placeholder Scan
- All code blocks are complete with real implementations.
- No "TBD" or "TODO" markers.

### Type Consistency
- `NovelEntityType` in schemas.py matches `NovelEntityType` used in routes and api.ts.
- `CustomEntitySummary` fields match what `list_custom_entities` returns.
- `CustomEntityDetail` fields match what `get_custom_entity_detail` returns.
- `resolve_custom_entity` returns `ResolvedEntity` (same as other resolve methods).
- `custom_entity_types` parameter name is consistent across `build_system_prompt`, `build_user_prompt`, `extract_chapter`, `extract_chunk`, `_run_llm_pass`, and `process_chapter`.
