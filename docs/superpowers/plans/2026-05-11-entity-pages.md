# Entity Pages (Locations, Objects, Factions) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add list + detail pages for locations, objects, and factions, with object ownership from the relationships table, and fix the pipeline to correctly resolve cross-entity relationships.

**Architecture:** Ten tasks: (1) pipeline fix for `resolve_any_entity`, (2) Pydantic schemas, (3–5) backend queries + API routes per entity type, (6) frontend TypeScript types + api functions, (7–9) frontend route files per entity type, (10) App.tsx + Sidebar.tsx wiring. Backend follows the existing `characters.py` / `queries.py` / `schemas.py` pattern throughout.

**Tech Stack:** FastAPI, Pydantic, psycopg (backend); React, React Query, React Router, TypeScript (frontend); pytest + FakeDB (tests).

---

## File Map

| File | Action |
|------|--------|
| `backend/pipeline/extraction/resolver.py` | Add `resolve_any_entity` method |
| `backend/pipeline/pipeline.py` | Use `resolve_any_entity` for relationship/dynamics loops |
| `backend/tests/test_resolver.py` | Add test for `resolve_any_entity` |
| `backend/api/schemas.py` | Add 7 new schema classes |
| `backend/api/queries.py` | Add 6 query functions |
| `backend/api/routes/locations.py` | New: 2 endpoints |
| `backend/api/routes/objects.py` | New: 2 endpoints |
| `backend/api/routes/factions.py` | New: 2 endpoints |
| `backend/api/app.py` | Register 3 new routers |
| `backend/tests/api/conftest.py` | Add `factions` to FakeDB |
| `backend/tests/api/test_locations.py` | New: API tests |
| `backend/tests/api/test_objects.py` | New: API tests |
| `backend/tests/api/test_factions.py` | New: API tests |
| `frontend/src/api.ts` | Add 6 types + 6 api functions |
| `frontend/src/routes/Locations.tsx` | New |
| `frontend/src/routes/LocationDetail.tsx` | New |
| `frontend/src/routes/Objects.tsx` | New |
| `frontend/src/routes/ObjectDetail.tsx` | New |
| `frontend/src/routes/Factions.tsx` | New |
| `frontend/src/routes/FactionDetail.tsx` | New |
| `frontend/src/App.tsx` | Add 6 routes |
| `frontend/src/components/Sidebar.tsx` | Add 3 links |

---

### Task 1: Add `resolve_any_entity` to EntityResolver and fix pipeline

**Files:**
- Modify: `backend/pipeline/extraction/resolver.py`
- Modify: `backend/pipeline/pipeline.py`
- Test: `backend/tests/test_resolver.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_resolver.py`:

```python
def test_resolve_any_entity_finds_entity_by_name():
    """resolve_any_entity returns the entity's universal ID from the entities table."""
    import uuid
    from pipeline.extraction.resolver import EntityResolver

    obj_entity_id = str(uuid.uuid4())

    class EntityDB:
        def __init__(self):
            self.entities = [
                {"id": obj_entity_id, "novel_id": "novel-1", "entity_type": "object", "name": "the One Ring"}
            ]
            self.executed = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            return None  # no exact name / alias hit via characters table

        def fetchval(self, query, params=None, *, commit=False):
            return None

        def execute(self, query, params=None):
            self.executed.append((query, params))

    db = EntityDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    uid = resolver.resolve_any_entity("the One Ring")
    assert uid == obj_entity_id


def test_resolve_any_entity_falls_back_to_character():
    """resolve_any_entity falls back to resolve_character when entity not in entities table."""
    import uuid
    from pipeline.extraction.resolver import EntityResolver

    char_entity_id = str(uuid.uuid4())
    char_id = str(uuid.uuid4())

    class FallbackDB:
        def __init__(self):
            self.entities = []  # empty — will fall through to resolve_character
            self.executed = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            # exact name match in characters table
            if "lower(name) = lower" in query and "characters" in query:
                return (char_id, char_entity_id)
            return None

        def fetchval(self, query, params=None, *, commit=False):
            return None

        def execute(self, query, params=None):
            self.executed.append((query, params))

    db = FallbackDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    uid = resolver.resolve_any_entity("Frodo")
    assert uid == char_entity_id
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd backend && uv run pytest tests/test_resolver.py::test_resolve_any_entity_finds_entity_by_name tests/test_resolver.py::test_resolve_any_entity_falls_back_to_character -v
```

Expected: AttributeError — `resolve_any_entity` not defined yet.

- [ ] **Step 3: Implement `resolve_any_entity` in resolver.py**

Add this method to the `EntityResolver` class in `backend/pipeline/extraction/resolver.py`, after `resolve_object`:

```python
    def resolve_any_entity(self, name: str) -> str:
        """Return the universal entity ID for any entity type.

        Looks up the entities table by name first. Falls back to
        resolve_character if not found, preserving backward-compat for
        purely character-to-character relationships.
        """
        normalized = (name or "").strip()
        if not normalized:
            raise ValueError("Cannot resolve empty entity name")

        cache_key = ("any", normalized.lower())
        cached = self._cache.get(cache_key)
        if cached:
            return cached[1]

        if hasattr(self.db, "entities"):
            entity = next(
                (
                    e for e in self.db.entities
                    if str(e.get("novel_id")) == str(self.novel_id)
                    and str(e.get("name", "")).lower() == normalized.lower()
                ),
                None,
            )
            if entity:
                uid = str(entity["id"])
                self._cache[cache_key] = (uid, uid)
                return uid
        else:
            row = self.db.fetchone(
                """
                SELECT id FROM entities
                WHERE novel_id = %s AND lower(name) = lower(%s)
                LIMIT 1
                """,
                (self.novel_id, normalized),
            )
            if row:
                uid = str(row[0])
                self._cache[cache_key] = (uid, uid)
                return uid

        resolved = self.resolve_character(normalized)
        self._cache[cache_key] = (resolved.entity_id, resolved.universal_id)
        return resolved.universal_id
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd backend && uv run pytest tests/test_resolver.py -v
```

Expected: all 5 pass.

- [ ] **Step 5: Update pipeline.py to use resolve_any_entity**

In `backend/pipeline/pipeline.py`, find the `relationship_updates` loop (lines ~308–330). Replace:

```python
        a_universal = resolver.resolve_character(a_name).universal_id
        b_universal = resolver.resolve_character(b_name).universal_id
```

with:

```python
        a_universal = resolver.resolve_any_entity(a_name)
        b_universal = resolver.resolve_any_entity(b_name)
```

Find the `dynamics_updates` loop (lines ~332–345). Replace the same two lines there too:

```python
        a_universal = resolver.resolve_any_entity(a_name)
        b_universal = resolver.resolve_any_entity(b_name)
```

- [ ] **Step 6: Run full test suite**

```bash
cd backend && uv run pytest -v
```

Expected: all tests pass (no regressions).

- [ ] **Step 7: Commit**

```bash
git add backend/pipeline/extraction/resolver.py backend/pipeline/pipeline.py backend/tests/test_resolver.py
git commit -m "feat: add resolve_any_entity; fix cross-entity relationship resolution in pipeline"
```

---

### Task 2: Add entity schemas

**Files:**
- Modify: `backend/api/schemas.py`

- [ ] **Step 1: Add 7 new schema classes**

Append to `backend/api/schemas.py` (after the existing `JobStatusResponse` class):

```python
class LocationSummary(BaseModel):
    id: UUID
    name: str
    aliases: list[str]
    description: str | None
    first_appearance_chapter: int | None


class LocationDetail(BaseModel):
    identity: LocationSummary
    events: list[TimelineEvent]
    characters: list[str]


class ObjectSummary(BaseModel):
    id: UUID
    name: str
    aliases: list[str]
    description: str | None
    significance: str | None
    first_appearance_chapter: int | None


class ObjectRelationship(BaseModel):
    character_name: str
    rel_type: str | None
    from_chapter: int | None
    to_chapter: int | None
    notes: str | None


class ObjectDetail(BaseModel):
    identity: ObjectSummary
    events: list[TimelineEvent]
    characters: list[str]
    relationships: list[ObjectRelationship]


class FactionSummary(BaseModel):
    id: UUID
    name: str
    aliases: list[str]
    description: str | None


class FactionDetail(BaseModel):
    identity: FactionSummary
    events: list[TimelineEvent]
    characters: list[str]
```

- [ ] **Step 2: Commit**

```bash
git add backend/api/schemas.py
git commit -m "feat: add Location, Object, Faction schemas"
```

---

### Task 3: Add factions to FakeDB + location queries + location API route + tests

**Files:**
- Modify: `backend/tests/api/conftest.py`
- Modify: `backend/api/queries.py`
- Create: `backend/api/routes/locations.py`
- Create: `backend/tests/api/test_locations.py`

- [ ] **Step 1: Add `factions` to FakeDB in conftest.py**

In `backend/tests/api/conftest.py`, add `factions` to the `FakeDB.__init__` signature and body:

```python
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
```

- [ ] **Step 2: Write failing tests for location endpoints**

Create `backend/tests/api/test_locations.py`:

```python
from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def _make_location(novel_id, name="Pemberley", **overrides):
    base = {
        "id": uuid4(),
        "novel_id": novel_id,
        "name": name,
        "aliases": [],
        "description": "A grand estate.",
        "first_appearance_chapter": 1,
    }
    base.update(overrides)
    return base


def test_list_locations(fake_db_factory, client):
    novel = make_novel()
    loc = _make_location(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        locations=[loc],
    )
    response = client.get(f"/api/novels/{novel['id']}/locations")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Pemberley"
    assert body[0]["first_appearance_chapter"] == 1


def test_list_locations_respects_cap(fake_db_factory, client):
    novel = make_novel()
    early = _make_location(novel["id"], name="Longbourn", first_appearance_chapter=1)
    late = _make_location(novel["id"], name="Pemberley", first_appearance_chapter=5)
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1), make_chapter(novel["id"], 5)],
        locations=[early, late],
    )
    response = client.get(f"/api/novels/{novel['id']}/locations?cap=3")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Longbourn"


def test_get_location_detail(fake_db_factory, client):
    novel = make_novel()
    loc = _make_location(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        locations=[loc],
    )
    response = client.get(f"/api/novels/{novel['id']}/locations/{loc['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == "Pemberley"
    assert body["events"] == []
    assert body["characters"] == []


def test_get_location_detail_404(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[make_chapter(novel["id"], 1)])
    response = client.get(f"/api/novels/{novel['id']}/locations/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
```

- [ ] **Step 3: Run to confirm failure**

```bash
cd backend && uv run pytest tests/api/test_locations.py -v
```

Expected: 4 FAILs — 404 (endpoint not registered).

- [ ] **Step 4: Add location query functions to queries.py**

Append to `backend/api/queries.py`:

```python
def list_locations(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "locations"):
        rows = [
            loc for loc in db.locations
            if loc["novel_id"] == novel_id
            and (loc.get("first_appearance_chapter") is None or loc["first_appearance_chapter"] <= effective_cap)
        ]
    else:
        rows = [
            dict(r)
            for r in db.fetchall(
                """
                SELECT id, name, aliases, description, first_appearance_chapter
                FROM locations
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


def get_location_detail(novel_id: UUID, location_id: UUID, cap: int | None) -> dict[str, Any] | None:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "locations"):
        loc = next(
            (l for l in db.locations if l["id"] == location_id and l["novel_id"] == novel_id),
            None,
        )
        if loc is None:
            return None
        char_name = {c["id"]: c["name"] for c in db.characters}
        loc_name = {l["id"]: l["name"] for l in db.locations}
        obj_name = {o["id"]: o["name"] for o in db.objects}
        char_ids_at_loc = {cs["character_id"] for cs in db.character_states if cs.get("location_id") == location_id}
        events = [
            {
                "id": e["id"],
                "chapter_number": e.get("chapter_number", 0),
                "description": e["description"],
                "event_type": e.get("event_type"),
                "impact_level": e.get("impact_level"),
                "involved_characters": [char_name.get(cid, str(cid)) for cid in (e.get("involved_characters") or [])],
                "involved_locations": [loc_name.get(lid, str(lid)) for lid in (e.get("involved_locations") or [])],
                "involved_objects": [obj_name.get(oid, str(oid)) for oid in (e.get("involved_objects") or [])],
            }
            for e in db.events
            if location_id in (e.get("involved_locations") or [])
        ]
        characters = sorted(char_name.get(cid, str(cid)) for cid in char_ids_at_loc)
    else:
        row = db.fetchone(
            """
            SELECT id, name, aliases, description, first_appearance_chapter
            FROM locations WHERE novel_id = %s AND id = %s
            """,
            (str(novel_id), str(location_id)),
            dict_rows=True,
        )
        if row is None:
            return None
        loc = dict(row)

        char_name_rows = db.fetchall("SELECT id, name FROM characters WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        loc_name_rows = db.fetchall("SELECT id, name FROM locations WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        obj_name_rows = db.fetchall("SELECT id, name FROM objects WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        char_name = {r["id"]: r["name"] for r in char_name_rows}
        loc_name = {r["id"]: r["name"] for r in loc_name_rows}
        obj_name = {r["id"]: r["name"] for r in obj_name_rows}

        event_rows = db.fetchall(
            """
            SELECT e.id, e.description, e.event_type, e.impact_level,
                   ch.number AS chapter_number,
                   e.involved_characters, e.involved_locations, e.involved_objects
            FROM events e
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE ch.novel_id = %s AND ch.number <= %s
              AND %s::uuid = ANY(e.involved_locations)
            ORDER BY ch.number
            """,
            (str(novel_id), effective_cap, str(location_id)),
            dict_rows=True,
        )
        events = [
            {
                "id": r["id"],
                "chapter_number": r["chapter_number"],
                "description": r["description"],
                "event_type": r.get("event_type"),
                "impact_level": r.get("impact_level"),
                "involved_characters": [char_name.get(cid, str(cid)) for cid in (r.get("involved_characters") or [])],
                "involved_locations": [loc_name.get(lid, str(lid)) for lid in (r.get("involved_locations") or [])],
                "involved_objects": [obj_name.get(oid, str(oid)) for oid in (r.get("involved_objects") or [])],
            }
            for r in event_rows
        ]

        char_rows = db.fetchall(
            """
            SELECT DISTINCT c.name
            FROM character_states cs
            JOIN characters c ON c.id = cs.character_id
            JOIN chapters ch ON ch.id = cs.chapter_id
            WHERE c.novel_id = %s AND cs.location_id = %s AND ch.number <= %s
            ORDER BY c.name
            """,
            (str(novel_id), str(location_id), effective_cap),
            dict_rows=True,
        )
        characters = [r["name"] for r in char_rows]

    return {
        "identity": {
            "id": loc["id"],
            "name": loc["name"],
            "aliases": list(loc.get("aliases") or []),
            "description": loc.get("description"),
            "first_appearance_chapter": loc.get("first_appearance_chapter"),
        },
        "events": events,
        "characters": characters,
    }
```

- [ ] **Step 5: Create locations API route**

Create `backend/api/routes/locations.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api import queries
from api.schemas import LocationDetail, LocationSummary

router = APIRouter(prefix="/api/novels/{novel_id}/locations", tags=["locations"])


@router.get("", response_model=list[LocationSummary])
def list_locations(novel_id: UUID, cap: int | None = Query(default=None)) -> list[LocationSummary]:
    return [LocationSummary(**row) for row in queries.list_locations(novel_id, cap)]


@router.get("/{location_id}", response_model=LocationDetail)
def get_location(
    novel_id: UUID,
    location_id: UUID,
    cap: int | None = Query(default=None),
) -> LocationDetail:
    detail = queries.get_location_detail(novel_id, location_id, cap)
    if detail is None:
        raise HTTPException(status_code=404, detail="Location not found")
    return LocationDetail(**detail)
```

- [ ] **Step 6: Register in app.py**

In `backend/api/app.py`, add to imports:

```python
from api.routes import characters, chapters, continuity, dynamics, locations, novels, process, relationships, threads, timeline
```

And after `app.include_router(characters.router)`:

```python
app.include_router(locations.router)
```

- [ ] **Step 7: Run location tests**

```bash
cd backend && uv run pytest tests/api/test_locations.py -v
```

Expected: 4 PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/tests/api/conftest.py backend/api/queries.py backend/api/routes/locations.py backend/api/app.py backend/tests/api/test_locations.py
git commit -m "feat: add location list and detail API endpoints"
```

---

### Task 4: Object queries + API route + tests (including ownership)

**Files:**
- Modify: `backend/api/queries.py`
- Create: `backend/api/routes/objects.py`
- Create: `backend/tests/api/test_objects.py`
- Modify: `backend/api/app.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/api/test_objects.py`:

```python
from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def _make_object(novel_id, name="the One Ring", **overrides):
    base = {
        "id": uuid4(),
        "novel_id": novel_id,
        "entity_id": uuid4(),
        "name": name,
        "aliases": [],
        "description": "A ring of power.",
        "significance": "MacGuffin",
        "first_appearance_chapter": 1,
    }
    base.update(overrides)
    return base


def test_list_objects(fake_db_factory, client):
    novel = make_novel()
    obj = _make_object(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        objects=[obj],
    )
    response = client.get(f"/api/novels/{novel['id']}/objects")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "the One Ring"
    assert body[0]["significance"] == "MacGuffin"


def test_list_objects_respects_cap(fake_db_factory, client):
    novel = make_novel()
    early = _make_object(novel["id"], name="sword", first_appearance_chapter=1)
    late = _make_object(novel["id"], name="shield", first_appearance_chapter=5)
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1), make_chapter(novel["id"], 5)],
        objects=[early, late],
    )
    response = client.get(f"/api/novels/{novel['id']}/objects?cap=3")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "sword"


def test_get_object_detail(fake_db_factory, client):
    novel = make_novel()
    obj = _make_object(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        objects=[obj],
    )
    response = client.get(f"/api/novels/{novel['id']}/objects/{obj['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == "the One Ring"
    assert body["identity"]["significance"] == "MacGuffin"
    assert body["events"] == []
    assert body["characters"] == []
    assert body["relationships"] == []


def test_get_object_detail_includes_ownership(fake_db_factory, client):
    novel = make_novel()
    char_entity_id = uuid4()
    char_id = uuid4()
    obj_entity_id = uuid4()
    obj = _make_object(novel["id"], entity_id=obj_entity_id)
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        objects=[obj],
        characters=[{
            "id": char_id,
            "novel_id": novel["id"],
            "entity_id": char_entity_id,
            "name": "Frodo",
            "aliases": [],
            "description": None,
            "first_appearance_chapter": 1,
        }],
        relationships=[{
            "entity_a_id": char_entity_id,
            "entity_b_id": obj_entity_id,
            "rel_type": "carries",
            "from_chapter": 1,
            "to_chapter": None,
            "notes": None,
        }],
    )
    response = client.get(f"/api/novels/{novel['id']}/objects/{obj['id']}")
    assert response.status_code == 200
    body = response.json()
    assert len(body["relationships"]) == 1
    assert body["relationships"][0]["character_name"] == "Frodo"
    assert body["relationships"][0]["rel_type"] == "carries"


def test_get_object_detail_404(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[make_chapter(novel["id"], 1)])
    response = client.get(f"/api/novels/{novel['id']}/objects/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd backend && uv run pytest tests/api/test_objects.py -v
```

Expected: 5 FAILs.

- [ ] **Step 3: Add object query functions to queries.py**

Append to `backend/api/queries.py`:

```python
def list_objects(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "objects"):
        rows = [
            o for o in db.objects
            if o["novel_id"] == novel_id
            and (o.get("first_appearance_chapter") is None or o["first_appearance_chapter"] <= effective_cap)
        ]
    else:
        rows = [
            dict(r)
            for r in db.fetchall(
                """
                SELECT id, name, aliases, description, significance, first_appearance_chapter
                FROM objects
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
            "significance": r.get("significance"),
            "first_appearance_chapter": r.get("first_appearance_chapter"),
        }
        for r in rows
    ]


def get_object_detail(novel_id: UUID, object_id: UUID, cap: int | None) -> dict[str, Any] | None:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "objects"):
        obj = next(
            (o for o in db.objects if o["id"] == object_id and o["novel_id"] == novel_id),
            None,
        )
        if obj is None:
            return None
        obj_entity_id = obj.get("entity_id")
        char_name = {c["id"]: c["name"] for c in db.characters}
        char_entity_name = {c["entity_id"]: c["name"] for c in db.characters if c.get("entity_id")}
        loc_name = {l["id"]: l["name"] for l in db.locations}
        obj_name_map = {o["id"]: o["name"] for o in db.objects}
        events = [
            {
                "id": e["id"],
                "chapter_number": e.get("chapter_number", 0),
                "description": e["description"],
                "event_type": e.get("event_type"),
                "impact_level": e.get("impact_level"),
                "involved_characters": [char_name.get(cid, str(cid)) for cid in (e.get("involved_characters") or [])],
                "involved_locations": [loc_name.get(lid, str(lid)) for lid in (e.get("involved_locations") or [])],
                "involved_objects": [obj_name_map.get(oid, str(oid)) for oid in (e.get("involved_objects") or [])],
            }
            for e in db.events
            if object_id in (e.get("involved_objects") or [])
        ]
        involved_char_ids: set[Any] = set()
        for e in db.events:
            if object_id in (e.get("involved_objects") or []):
                involved_char_ids.update(e.get("involved_characters") or [])
        characters = sorted(char_name.get(cid, str(cid)) for cid in involved_char_ids)
        relationships = [
            {
                "character_name": char_entity_name.get(r["entity_a_id"], str(r["entity_a_id"])),
                "rel_type": r.get("rel_type"),
                "from_chapter": r.get("from_chapter"),
                "to_chapter": r.get("to_chapter"),
                "notes": r.get("notes"),
            }
            for r in db.relationships
            if r.get("entity_b_id") == obj_entity_id and obj_entity_id is not None
        ]
    else:
        row = db.fetchone(
            """
            SELECT id, name, aliases, description, significance,
                   first_appearance_chapter, entity_id
            FROM objects WHERE novel_id = %s AND id = %s
            """,
            (str(novel_id), str(object_id)),
            dict_rows=True,
        )
        if row is None:
            return None
        obj = dict(row)

        char_name_rows = db.fetchall("SELECT id, name FROM characters WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        loc_name_rows = db.fetchall("SELECT id, name FROM locations WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        obj_name_rows = db.fetchall("SELECT id, name FROM objects WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        char_name = {r["id"]: r["name"] for r in char_name_rows}
        loc_name = {r["id"]: r["name"] for r in loc_name_rows}
        obj_name_map = {r["id"]: r["name"] for r in obj_name_rows}

        event_rows = db.fetchall(
            """
            SELECT e.id, e.description, e.event_type, e.impact_level,
                   ch.number AS chapter_number,
                   e.involved_characters, e.involved_locations, e.involved_objects
            FROM events e
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE ch.novel_id = %s AND ch.number <= %s
              AND %s::uuid = ANY(e.involved_objects)
            ORDER BY ch.number
            """,
            (str(novel_id), effective_cap, str(object_id)),
            dict_rows=True,
        )
        events = [
            {
                "id": r["id"],
                "chapter_number": r["chapter_number"],
                "description": r["description"],
                "event_type": r.get("event_type"),
                "impact_level": r.get("impact_level"),
                "involved_characters": [char_name.get(cid, str(cid)) for cid in (r.get("involved_characters") or [])],
                "involved_locations": [loc_name.get(lid, str(lid)) for lid in (r.get("involved_locations") or [])],
                "involved_objects": [obj_name_map.get(oid, str(oid)) for oid in (r.get("involved_objects") or [])],
            }
            for r in event_rows
        ]

        char_ids_in_events: set[str] = set()
        for r in event_rows:
            char_ids_in_events.update(str(c) for c in (r.get("involved_characters") or []))
        characters = sorted(char_name.get(cid, cid) for cid in char_ids_in_events)

        rel_rows = db.fetchall(
            """
            SELECT c.name AS character_name, r.rel_type, r.from_chapter, r.to_chapter, r.notes
            FROM relationships r
            JOIN characters c ON c.entity_id = r.entity_a_id AND c.novel_id = %s
            WHERE r.entity_b_id = (
                SELECT entity_id FROM objects WHERE id = %s AND novel_id = %s
            )
            ORDER BY r.from_chapter NULLS LAST
            """,
            (str(novel_id), str(object_id), str(novel_id)),
            dict_rows=True,
        )
        relationships = [dict(r) for r in rel_rows]

    return {
        "identity": {
            "id": obj["id"],
            "name": obj["name"],
            "aliases": list(obj.get("aliases") or []),
            "description": obj.get("description"),
            "significance": obj.get("significance"),
            "first_appearance_chapter": obj.get("first_appearance_chapter"),
        },
        "events": events,
        "characters": characters,
        "relationships": relationships,
    }
```

- [ ] **Step 4: Create objects API route**

Create `backend/api/routes/objects.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api import queries
from api.schemas import ObjectDetail, ObjectSummary

router = APIRouter(prefix="/api/novels/{novel_id}/objects", tags=["objects"])


@router.get("", response_model=list[ObjectSummary])
def list_objects(novel_id: UUID, cap: int | None = Query(default=None)) -> list[ObjectSummary]:
    return [ObjectSummary(**row) for row in queries.list_objects(novel_id, cap)]


@router.get("/{object_id}", response_model=ObjectDetail)
def get_object(
    novel_id: UUID,
    object_id: UUID,
    cap: int | None = Query(default=None),
) -> ObjectDetail:
    detail = queries.get_object_detail(novel_id, object_id, cap)
    if detail is None:
        raise HTTPException(status_code=404, detail="Object not found")
    return ObjectDetail(**detail)
```

- [ ] **Step 5: Register in app.py**

Update import in `backend/api/app.py`:

```python
from api.routes import characters, chapters, continuity, dynamics, locations, novels, objects, process, relationships, threads, timeline
```

Add after `app.include_router(locations.router)`:

```python
app.include_router(objects.router)
```

- [ ] **Step 6: Run object tests**

```bash
cd backend && uv run pytest tests/api/test_objects.py -v
```

Expected: 5 PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/api/queries.py backend/api/routes/objects.py backend/api/app.py backend/tests/api/test_objects.py
git commit -m "feat: add object list and detail API endpoints with ownership"
```

---

### Task 5: Faction queries + API route + tests

**Files:**
- Modify: `backend/api/queries.py`
- Create: `backend/api/routes/factions.py`
- Create: `backend/tests/api/test_factions.py`
- Modify: `backend/api/app.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/api/test_factions.py`:

```python
from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def _make_faction(novel_id, name="The Order", **overrides):
    base = {
        "id": uuid4(),
        "novel_id": novel_id,
        "name": name,
        "aliases": [],
        "description": "A secret society.",
    }
    base.update(overrides)
    return base


def test_list_factions(fake_db_factory, client):
    novel = make_novel()
    faction = _make_faction(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        factions=[faction],
    )
    response = client.get(f"/api/novels/{novel['id']}/factions")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "The Order"


def test_get_faction_detail(fake_db_factory, client):
    novel = make_novel()
    faction = _make_faction(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        factions=[faction],
    )
    response = client.get(f"/api/novels/{novel['id']}/factions/{faction['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == "The Order"
    assert body["events"] == []
    assert body["characters"] == []


def test_get_faction_detail_404(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[make_chapter(novel["id"], 1)])
    response = client.get(f"/api/novels/{novel['id']}/factions/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd backend && uv run pytest tests/api/test_factions.py -v
```

Expected: 3 FAILs.

- [ ] **Step 3: Add faction query functions to queries.py**

Append to `backend/api/queries.py`:

```python
def list_factions(novel_id: UUID) -> list[dict[str, Any]]:
    db = _get_db()
    if hasattr(db, "factions"):
        rows = [f for f in db.factions if f["novel_id"] == novel_id]
    else:
        rows = [
            dict(r)
            for r in db.fetchall(
                """
                SELECT id, name, aliases, description
                FROM factions
                WHERE novel_id = %s
                ORDER BY name
                """,
                (str(novel_id),),
                dict_rows=True,
            )
        ]
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "aliases": list(r.get("aliases") or []),
            "description": r.get("description"),
        }
        for r in rows
    ]


def get_faction_detail(novel_id: UUID, faction_id: UUID) -> dict[str, Any] | None:
    db = _get_db()
    if hasattr(db, "factions"):
        faction = next(
            (f for f in db.factions if f["id"] == faction_id and f["novel_id"] == novel_id),
            None,
        )
        if faction is None:
            return None
    else:
        row = db.fetchone(
            """
            SELECT id, name, aliases, description
            FROM factions WHERE novel_id = %s AND id = %s
            """,
            (str(novel_id), str(faction_id)),
            dict_rows=True,
        )
        if row is None:
            return None
        faction = dict(row)

    return {
        "identity": {
            "id": faction["id"],
            "name": faction["name"],
            "aliases": list(faction.get("aliases") or []),
            "description": faction.get("description"),
        },
        "events": [],
        "characters": [],
    }
```

- [ ] **Step 4: Create factions API route**

Create `backend/api/routes/factions.py`:

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from api import queries
from api.schemas import FactionDetail, FactionSummary

router = APIRouter(prefix="/api/novels/{novel_id}/factions", tags=["factions"])


@router.get("", response_model=list[FactionSummary])
def list_factions(novel_id: UUID) -> list[FactionSummary]:
    return [FactionSummary(**row) for row in queries.list_factions(novel_id)]


@router.get("/{faction_id}", response_model=FactionDetail)
def get_faction(novel_id: UUID, faction_id: UUID) -> FactionDetail:
    detail = queries.get_faction_detail(novel_id, faction_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Faction not found")
    return FactionDetail(**detail)
```

- [ ] **Step 5: Register in app.py**

Update import in `backend/api/app.py`:

```python
from api.routes import characters, chapters, continuity, dynamics, factions, locations, novels, objects, process, relationships, threads, timeline
```

Add after `app.include_router(objects.router)`:

```python
app.include_router(factions.router)
```

- [ ] **Step 6: Run faction tests + full suite**

```bash
cd backend && uv run pytest tests/api/test_factions.py -v && uv run pytest -q
```

Expected: 3 faction tests PASS; full suite all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/api/queries.py backend/api/routes/factions.py backend/api/app.py backend/tests/api/test_factions.py
git commit -m "feat: add faction list and detail API endpoints"
```

---

### Task 6: Frontend TypeScript types and api functions

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add types and api functions to api.ts**

In `frontend/src/api.ts`, add after the existing type definitions:

```typescript
export type LocationSummary = {
  id: string;
  name: string;
  aliases: string[];
  description: string | null;
  first_appearance_chapter: number | null;
};

export type LocationDetail = {
  identity: LocationSummary;
  events: TimelineEvent[];
  characters: string[];
};

export type ObjectSummary = {
  id: string;
  name: string;
  aliases: string[];
  description: string | null;
  significance: string | null;
  first_appearance_chapter: number | null;
};

export type ObjectRelationship = {
  character_name: string;
  rel_type: string | null;
  from_chapter: number | null;
  to_chapter: number | null;
  notes: string | null;
};

export type ObjectDetail = {
  identity: ObjectSummary;
  events: TimelineEvent[];
  characters: string[];
  relationships: ObjectRelationship[];
};

export type FactionSummary = {
  id: string;
  name: string;
  aliases: string[];
  description: string | null;
};

export type FactionDetail = {
  identity: FactionSummary;
  events: TimelineEvent[];
  characters: string[];
};
```

Add to the `api` object:

```typescript
  locations: (novelId: string, cap: number | null) =>
    fetchJson<LocationSummary[]>(`/api/novels/${novelId}/locations${capParam(cap)}`),
  location: (novelId: string, locationId: string, cap: number | null) =>
    fetchJson<LocationDetail>(`/api/novels/${novelId}/locations/${locationId}${capParam(cap)}`),
  objects: (novelId: string, cap: number | null) =>
    fetchJson<ObjectSummary[]>(`/api/novels/${novelId}/objects${capParam(cap)}`),
  object: (novelId: string, objectId: string, cap: number | null) =>
    fetchJson<ObjectDetail>(`/api/novels/${novelId}/objects/${objectId}${capParam(cap)}`),
  factions: (novelId: string) =>
    fetchJson<FactionSummary[]>(`/api/novels/${novelId}/factions`),
  faction: (novelId: string, factionId: string) =>
    fetchJson<FactionDetail>(`/api/novels/${novelId}/factions/${factionId}`),
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

Expected: build succeeds, no TS errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat: add Location, Object, Faction TypeScript types and api functions"
```

---

### Task 7: Locations.tsx + LocationDetail.tsx

**Files:**
- Create: `frontend/src/routes/Locations.tsx`
- Create: `frontend/src/routes/LocationDetail.tsx`

- [ ] **Step 1: Create Locations.tsx**

```tsx
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Locations() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const location = useLocation();
  const { data, isLoading, error } = useQuery({
    queryKey: ["locations", novelId, cap],
    queryFn: () => api.locations(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <p>No locations.</p>;
  return (
    <div>
      <h1>Locations</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Aliases</th>
            <th>Description</th>
            <th>First seen</th>
          </tr>
        </thead>
        <tbody>
          {data.map((l) => (
            <tr key={l.id}>
              <td>
                <Link to={`/novels/${novelId}/locations/${l.id}${location.search}`}>{l.name}</Link>
              </td>
              <td>{l.aliases.join(", ") || "—"}</td>
              <td>{l.description ?? "—"}</td>
              <td>{l.first_appearance_chapter ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Create LocationDetail.tsx**

```tsx
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, type LocationDetail as Detail } from "../api";
import FieldList, { renderArray } from "../components/FieldList";
import { useChapterCap } from "../hooks/useChapterCap";

export default function LocationDetail() {
  const { novelId, locationId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading, error } = useQuery({
    queryKey: ["location", novelId, locationId, cap],
    queryFn: () => api.location(novelId!, locationId!, cap),
    enabled: Boolean(novelId && locationId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data) return <p>Not found.</p>;
  return (
    <article>
      <h1>{data.identity.name}</h1>
      <section>
        <h2>Identity</h2>
        <FieldList
          fields={[
            { label: "Aliases", value: renderArray(data.identity.aliases) },
            { label: "First seen", value: data.identity.first_appearance_chapter ?? null },
            { label: "Description", value: data.identity.description },
          ]}
        />
      </section>
      <section>
        <h2>Characters here ({data.characters.length})</h2>
        {data.characters.length === 0 ? (
          <p className="muted">None within cap.</p>
        ) : (
          <ul>
            {data.characters.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h2>Events ({data.events.length})</h2>
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
              </tr>
            </thead>
            <tbody>
              {data.events.map((e) => (
                <tr key={e.id}>
                  <td>{e.chapter_number}</td>
                  <td>{e.description}</td>
                  <td>{e.event_type ?? "—"}</td>
                  <td>{e.impact_level ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </article>
  );
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

Expected: no TS errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/Locations.tsx frontend/src/routes/LocationDetail.tsx
git commit -m "feat: add Locations list and detail pages"
```

---

### Task 8: Objects.tsx + ObjectDetail.tsx

**Files:**
- Create: `frontend/src/routes/Objects.tsx`
- Create: `frontend/src/routes/ObjectDetail.tsx`

- [ ] **Step 1: Create Objects.tsx**

```tsx
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Objects() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const location = useLocation();
  const { data, isLoading, error } = useQuery({
    queryKey: ["objects", novelId, cap],
    queryFn: () => api.objects(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <p>No objects.</p>;
  return (
    <div>
      <h1>Objects</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Aliases</th>
            <th>Significance</th>
            <th>Description</th>
            <th>First seen</th>
          </tr>
        </thead>
        <tbody>
          {data.map((o) => (
            <tr key={o.id}>
              <td>
                <Link to={`/novels/${novelId}/objects/${o.id}${location.search}`}>{o.name}</Link>
              </td>
              <td>{o.aliases.join(", ") || "—"}</td>
              <td>{o.significance ?? "—"}</td>
              <td>{o.description ?? "—"}</td>
              <td>{o.first_appearance_chapter ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Create ObjectDetail.tsx**

```tsx
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, type ObjectDetail as Detail } from "../api";
import FieldList, { renderArray } from "../components/FieldList";
import { useChapterCap } from "../hooks/useChapterCap";

export default function ObjectDetail() {
  const { novelId, objectId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading, error } = useQuery({
    queryKey: ["object", novelId, objectId, cap],
    queryFn: () => api.object(novelId!, objectId!, cap),
    enabled: Boolean(novelId && objectId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data) return <p>Not found.</p>;
  return (
    <article>
      <h1>{data.identity.name}</h1>
      <section>
        <h2>Identity</h2>
        <FieldList
          fields={[
            { label: "Aliases", value: renderArray(data.identity.aliases) },
            { label: "Significance", value: data.identity.significance },
            { label: "First seen", value: data.identity.first_appearance_chapter ?? null },
            { label: "Description", value: data.identity.description },
          ]}
        />
      </section>
      <section>
        <h2>Ownership / Relationships ({data.relationships.length})</h2>
        {data.relationships.length === 0 ? (
          <p className="muted">No recorded relationships.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Character</th>
                <th>Type</th>
                <th>From ch.</th>
                <th>To ch.</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {data.relationships.map((r, i) => (
                <tr key={i}>
                  <td>{r.character_name}</td>
                  <td>{r.rel_type ?? "—"}</td>
                  <td>{r.from_chapter ?? "—"}</td>
                  <td>{r.to_chapter ?? "ongoing"}</td>
                  <td>{r.notes ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section>
        <h2>Characters involved ({data.characters.length})</h2>
        {data.characters.length === 0 ? (
          <p className="muted">None within cap.</p>
        ) : (
          <ul>
            {data.characters.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h2>Events ({data.events.length})</h2>
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
              </tr>
            </thead>
            <tbody>
              {data.events.map((e) => (
                <tr key={e.id}>
                  <td>{e.chapter_number}</td>
                  <td>{e.description}</td>
                  <td>{e.event_type ?? "—"}</td>
                  <td>{e.impact_level ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </article>
  );
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

Expected: no TS errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/Objects.tsx frontend/src/routes/ObjectDetail.tsx
git commit -m "feat: add Objects list and detail pages with ownership section"
```

---

### Task 9: Factions.tsx + FactionDetail.tsx

**Files:**
- Create: `frontend/src/routes/Factions.tsx`
- Create: `frontend/src/routes/FactionDetail.tsx`

- [ ] **Step 1: Create Factions.tsx**

```tsx
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { api } from "../api";

export default function Factions() {
  const { novelId } = useParams();
  const location = useLocation();
  const { data, isLoading, error } = useQuery({
    queryKey: ["factions", novelId],
    queryFn: () => api.factions(novelId!),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <p>No factions.</p>;
  return (
    <div>
      <h1>Factions</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Aliases</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          {data.map((f) => (
            <tr key={f.id}>
              <td>
                <Link to={`/novels/${novelId}/factions/${f.id}${location.search}`}>{f.name}</Link>
              </td>
              <td>{f.aliases.join(", ") || "—"}</td>
              <td>{f.description ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Create FactionDetail.tsx**

```tsx
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, type FactionDetail as Detail } from "../api";
import FieldList, { renderArray } from "../components/FieldList";

export default function FactionDetail() {
  const { novelId, factionId } = useParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ["faction", novelId, factionId],
    queryFn: () => api.faction(novelId!, factionId!),
    enabled: Boolean(novelId && factionId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data) return <p>Not found.</p>;
  return (
    <article>
      <h1>{data.identity.name}</h1>
      <section>
        <h2>Identity</h2>
        <FieldList
          fields={[
            { label: "Aliases", value: renderArray(data.identity.aliases) },
            { label: "Description", value: data.identity.description },
          ]}
        />
      </section>
    </article>
  );
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

Expected: no TS errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/Factions.tsx frontend/src/routes/FactionDetail.tsx
git commit -m "feat: add Factions list and detail pages"
```

---

### Task 10: Wire App.tsx + Sidebar.tsx

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Update App.tsx**

Add imports after existing route imports:

```tsx
import FactionDetail from "./routes/FactionDetail";
import Factions from "./routes/Factions";
import LocationDetail from "./routes/LocationDetail";
import Locations from "./routes/Locations";
import ObjectDetail from "./routes/ObjectDetail";
import Objects from "./routes/Objects";
```

Add routes inside `<Routes>` after the existing `/dynamics` route:

```tsx
<Route path="/novels/:novelId/locations" element={<Layout><Locations /></Layout>} />
<Route path="/novels/:novelId/locations/:locationId" element={<Layout><LocationDetail /></Layout>} />
<Route path="/novels/:novelId/objects" element={<Layout><Objects /></Layout>} />
<Route path="/novels/:novelId/objects/:objectId" element={<Layout><ObjectDetail /></Layout>} />
<Route path="/novels/:novelId/factions" element={<Layout><Factions /></Layout>} />
<Route path="/novels/:novelId/factions/:factionId" element={<Layout><FactionDetail /></Layout>} />
```

- [ ] **Step 2: Update Sidebar.tsx**

In `Sidebar.tsx`, add three entries to the `links` array, after `["Relationships", ...]` and before `["Dynamics", ...]`:

```tsx
        ["Locations", `/novels/${novelId}/locations`],
        ["Objects", `/novels/${novelId}/objects`],
        ["Factions", `/novels/${novelId}/factions`],
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

Expected: build succeeds.

- [ ] **Step 4: Run full backend test suite**

```bash
cd backend && uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat: wire Locations, Objects, Factions routes and sidebar links"
```
