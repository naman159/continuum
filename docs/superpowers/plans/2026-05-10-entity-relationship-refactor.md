# Entity & Relationship Model Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a unified `entities` table, split `relationships` into structural bonds with temporal range, and add `shared_dynamics` for per-chapter relational climate.

**Architecture:** A new `entities` table serves as the identity layer for all entity types; each type table (characters, locations, factions, objects) gains an `entity_id` FK. `relationships` stores structural bonds with `from_chapter`/`to_chapter`. `shared_dynamics` stores per-chapter prose describing the relational climate between two entities. The `character_states.relationships` JSONB column is removed.

**Tech Stack:** PostgreSQL, Python (psycopg2 via DBClient), FastAPI/Pydantic, pytest

---

## File Map

**Modified:**
- `backend/pipeline/db/schema.sql` — add entities, shared_dynamics; redesign relationships; drop character_states.relationships
- `backend/pipeline/extraction/resolver.py` — populate entities table on creation; expose universal_id
- `backend/pipeline/extraction/prompts.py` — new passes: relationship_updates, dynamics_updates; remove relationships from entity_deltas
- `backend/pipeline/extraction/extractor.py` — handle new passes in compose, merge, normalize
- `backend/pipeline/pipeline.py` — persist new passes; drop character_states.relationships write
- `backend/api/schemas.py` — update CharacterRelationshipRow, CharacterStateRow; add SharedDynamicRow
- `backend/api/queries.py` — update relationship/character queries for new schema
- `backend/tests/api/conftest.py` — add shared_dynamics to FakeDB; update relationships fixture shape
- `backend/tests/api/test_relationships.py` — update fixture shape
- `backend/tests/api/test_characters.py` — remove relationships from character state fixtures

**Created:**
- `backend/pipeline/db/migrate_entity_refactor.py` — migration script for existing data
- `backend/api/routes/dynamics.py` — GET /novels/{id}/dynamics endpoint
- `backend/tests/api/test_dynamics.py` — tests for shared_dynamics endpoint

---

## Task 1: Schema — entities table, entity_id columns, shared_dynamics, redesigned relationships

**Files:**
- Modify: `backend/pipeline/db/schema.sql`

- [ ] **Step 1: Update schema.sql**

Replace the `relationships` table definition and add `entities` + `shared_dynamics`. The full new block:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS novels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    author TEXT,
    language TEXT DEFAULT 'en',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chapters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT,
    raw_text TEXT NOT NULL,
    summary TEXT,
    embedding VECTOR(__EMBEDDING_DIM__),
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(novel_id, number)
);

CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('character', 'location', 'faction', 'object')),
    name TEXT NOT NULL,
    UNIQUE(novel_id, entity_type, name)
);

CREATE INDEX IF NOT EXISTS idx_entities_novel ON entities(novel_id, entity_type);

CREATE TABLE IF NOT EXISTS characters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    aliases TEXT[] DEFAULT '{}',
    first_appearance_chapter INTEGER,
    description TEXT,
    embedding VECTOR(__EMBEDDING_DIM__),
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_characters_novel_name ON characters(novel_id, lower(name));

CREATE TABLE IF NOT EXISTS locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    description TEXT,
    parent_location_id UUID REFERENCES locations(id),
    first_appearance_chapter INTEGER,
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_locations_novel_name ON locations(novel_id, lower(name));

CREATE TABLE IF NOT EXISTS factions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    description TEXT,
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_factions_novel_name ON factions(novel_id, lower(name));

CREATE TABLE IF NOT EXISTS objects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    description TEXT,
    significance TEXT,
    first_appearance_chapter INTEGER,
    UNIQUE(novel_id, name)
);

CREATE INDEX IF NOT EXISTS idx_objects_novel_name ON objects(novel_id, lower(name));

CREATE TABLE IF NOT EXISTS character_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    location_id UUID REFERENCES locations(id),
    emotional_state TEXT,
    goals TEXT,
    knowledge TEXT[] DEFAULT '{}',
    physical_state TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_character_states_character_chapter
ON character_states(character_id, chapter_id);

CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    event_type TEXT,
    impact_level TEXT,
    involved_characters UUID[] DEFAULT '{}',
    involved_locations UUID[] DEFAULT '{}',
    involved_objects UUID[] DEFAULT '{}',
    embedding VECTOR(__EMBEDDING_DIM__),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_chapter ON events(chapter_id);

CREATE TABLE IF NOT EXISTS relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_a_id UUID NOT NULL REFERENCES entities(id),
    entity_b_id UUID NOT NULL REFERENCES entities(id),
    rel_type TEXT,
    from_chapter INTEGER,
    to_chapter INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_relationships_a ON relationships(entity_a_id);
CREATE INDEX IF NOT EXISTS idx_relationships_b ON relationships(entity_b_id);

CREATE TABLE IF NOT EXISTS shared_dynamics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_a_id UUID NOT NULL REFERENCES entities(id),
    entity_b_id UUID NOT NULL REFERENCES entities(id),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shared_dynamics_entities ON shared_dynamics(entity_a_id, entity_b_id);
CREATE INDEX IF NOT EXISTS idx_shared_dynamics_chapter ON shared_dynamics(chapter_id);

CREATE TABLE IF NOT EXISTS plot_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'open',
    opened_chapter INTEGER,
    closed_chapter INTEGER,
    thread_type TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(novel_id, title)
);

CREATE INDEX IF NOT EXISTS idx_plot_threads_novel_status ON plot_threads(novel_id, status);

CREATE TABLE IF NOT EXISTS thread_events (
    thread_id UUID NOT NULL REFERENCES plot_threads(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    impact TEXT,
    PRIMARY KEY (thread_id, event_id)
);

CREATE TABLE IF NOT EXISTS continuity_flags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    flag_type TEXT,
    resolved BOOLEAN DEFAULT false,
    resolved_chapter_id UUID REFERENCES chapters(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chapters_embedding
ON chapters USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_events_embedding
ON events USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

- [ ] **Step 2: Verify schema initialises cleanly on a fresh DB**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
uv run novel-webapp init-db
```

Expected: `{"status": "ok", "schema": null}` with no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/pipeline/db/schema.sql
git commit -m "feat(schema): add entities table, shared_dynamics, temporal relationships"
```

---

## Task 2: Migration script for existing data

**Files:**
- Create: `backend/pipeline/db/migrate_entity_refactor.py`

- [ ] **Step 1: Create the migration script**

```python
# backend/pipeline/db/migrate_entity_refactor.py
"""
One-shot migration: populate entities table, backfill entity_id FKs on type tables,
migrate relationships to use entities.id FKs, migrate character_states.relationships
JSONB to shared_dynamics rows.

Run: uv run python -m pipeline.db.migrate_entity_refactor
"""
from __future__ import annotations

import json
import sys

from pipeline.db.client import DBClient


ENTITY_TYPES = [
    ("character", "characters"),
    ("location", "locations"),
    ("faction", "factions"),
    ("object", "objects"),
]


def migrate(db: DBClient) -> None:
    print("Step 1: Populate entities table from type tables...")
    for entity_type, table in ENTITY_TYPES:
        db.execute(
            f"""
            INSERT INTO entities (novel_id, entity_type, name)
            SELECT novel_id, %s, name FROM {table}
            ON CONFLICT (novel_id, entity_type, name) DO NOTHING
            """,
            (entity_type,),
            commit=True,
        )
        print(f"  Inserted {entity_type} entities.")

    print("Step 2: Backfill entity_id on type tables...")
    for entity_type, table in ENTITY_TYPES:
        db.execute(
            f"""
            UPDATE {table} t
            SET entity_id = e.id
            FROM entities e
            WHERE e.novel_id = t.novel_id
              AND e.entity_type = %s
              AND e.name = t.name
              AND t.entity_id IS NULL
            """,
            (entity_type,),
            commit=True,
        )
        print(f"  Backfilled entity_id on {table}.")

    print("Step 3: Migrate relationships to use entities.id FKs...")
    # Add temp columns for new FKs
    try:
        db.execute("ALTER TABLE relationships ADD COLUMN IF NOT EXISTS new_entity_a_id UUID", commit=True)
        db.execute("ALTER TABLE relationships ADD COLUMN IF NOT EXISTS new_entity_b_id UUID", commit=True)
        db.execute("ALTER TABLE relationships ADD COLUMN IF NOT EXISTS from_chapter INTEGER", commit=True)
        db.execute("ALTER TABLE relationships ADD COLUMN IF NOT EXISTS to_chapter INTEGER", commit=True)
    except Exception as e:
        print(f"  Column addition skipped (may already exist): {e}")

    # Populate new_entity_a_id / new_entity_b_id using type-specific tables
    for entity_type, table in ENTITY_TYPES:
        db.execute(
            f"""
            UPDATE relationships r
            SET new_entity_a_id = t.entity_id
            FROM {table} t
            WHERE r.entity_a_type = %s
              AND r.entity_a_id = t.id
              AND r.new_entity_a_id IS NULL
            """,
            (entity_type,),
            commit=True,
        )
        db.execute(
            f"""
            UPDATE relationships r
            SET new_entity_b_id = t.entity_id
            FROM {table} t
            WHERE r.entity_b_type = %s
              AND r.entity_b_id = t.id
              AND r.new_entity_b_id IS NULL
            """,
            (entity_type,),
            commit=True,
        )

    # Populate from_chapter from chapter_id
    db.execute(
        """
        UPDATE relationships r
        SET from_chapter = ch.number
        FROM chapters ch
        WHERE ch.id = r.chapter_id
          AND r.from_chapter IS NULL
        """,
        commit=True,
    )

    # Drop old columns, rename new ones
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS entity_a_id", commit=True)
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS entity_b_id", commit=True)
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS entity_a_type", commit=True)
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS entity_b_type", commit=True)
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS status", commit=True)
    db.execute("ALTER TABLE relationships DROP COLUMN IF EXISTS chapter_id", commit=True)
    db.execute("ALTER TABLE relationships RENAME COLUMN new_entity_a_id TO entity_a_id", commit=True)
    db.execute("ALTER TABLE relationships RENAME COLUMN new_entity_b_id TO entity_b_id", commit=True)
    print("  relationships migrated.")

    print("Step 4: Migrate character_states.relationships JSONB to shared_dynamics...")
    rows = db.fetchall(
        """
        SELECT cs.id, cs.character_id, cs.chapter_id, cs.relationships
        FROM character_states cs
        WHERE cs.relationships IS NOT NULL AND cs.relationships != '{}'::jsonb
        """,
        dict_rows=True,
    )
    inserted = 0
    for row in rows:
        char_entity = db.fetchone(
            "SELECT entity_id FROM characters WHERE id = %s",
            (str(row["character_id"]),),
        )
        if not char_entity or not char_entity[0]:
            continue
        a_entity_id = str(char_entity[0])

        rels: dict = row["relationships"] if isinstance(row["relationships"], dict) else {}
        for target_name, description in rels.items():
            target = db.fetchone(
                """
                SELECT c.entity_id FROM characters c
                WHERE lower(c.name) = lower(%s)
                   OR EXISTS (SELECT 1 FROM unnest(c.aliases) alias WHERE lower(alias) = lower(%s))
                LIMIT 1
                """,
                (str(target_name), str(target_name)),
            )
            if not target or not target[0]:
                continue
            b_entity_id = str(target[0])
            db.execute(
                """
                INSERT INTO shared_dynamics (entity_a_id, entity_b_id, chapter_id, description)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (a_entity_id, b_entity_id, str(row["chapter_id"]), str(description)),
                commit=True,
            )
            inserted += 1
    print(f"  Inserted {inserted} shared_dynamics rows from character_states.")

    print("Step 5: Drop character_states.relationships column...")
    db.execute(
        "ALTER TABLE character_states DROP COLUMN IF EXISTS relationships",
        commit=True,
    )
    print("  Done.")


def main() -> None:
    print("Starting entity refactor migration...")
    with DBClient() as db:
        migrate(db)
    print("Migration complete.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add backend/pipeline/db/migrate_entity_refactor.py
git commit -m "feat(migration): entity refactor migration script"
```

---

## Task 3: Update EntityResolver to populate entities table

**Files:**
- Modify: `backend/pipeline/extraction/resolver.py`
- Test: `backend/tests/test_resolver.py` (create)

- [ ] **Step 1: Write a failing test**

Create `backend/tests/test_resolver.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from pipeline.extraction.resolver import EntityResolver, ResolvedEntity


class FakeDBForResolver:
    """Minimal fake DB for resolver tests."""

    def __init__(self) -> None:
        self._entities: list[dict] = []
        self._characters: list[dict] = []
        self._commit_calls: list = []

    def fetchone(self, sql: str, params=(), dict_rows: bool = False):
        sql_lower = sql.lower()
        if "from entities" in sql_lower:
            name = params[1] if len(params) > 1 else None
            novel_id = params[0]
            for e in self._entities:
                if str(e["novel_id"]) == str(novel_id) and e["name"].lower() == str(name).lower():
                    return (e["id"],) if not dict_rows else e
            return None
        if "from characters" in sql_lower and "entity_id" in sql_lower:
            char_id = params[0]
            for c in self._characters:
                if str(c["id"]) == str(char_id):
                    row = {"entity_id": c.get("entity_id")}
                    return (c.get("entity_id"),) if not dict_rows else row
            return None
        if "from characters" in sql_lower:
            novel_id = params[0]
            name = params[1] if len(params) > 1 else None
            for c in self._characters:
                if str(c["novel_id"]) == str(novel_id) and c["name"].lower() == str(name).lower():
                    return (c["id"], c.get("entity_id")) if not dict_rows else c
            return None
        return None

    def fetchval(self, sql: str, params=(), commit: bool = False):
        import uuid
        new_id = uuid.uuid4()
        sql_lower = sql.lower()
        if "insert into entities" in sql_lower:
            entity = {"id": new_id, "novel_id": params[0], "entity_type": params[1], "name": params[2]}
            self._entities.append(entity)
            return new_id
        if "insert into characters" in sql_lower:
            char = {"id": new_id, "novel_id": params[0], "name": params[1], "entity_id": params[2]}
            self._characters.append(char)
            return new_id
        return new_id

    def execute(self, sql: str, params=(), commit: bool = False):
        pass


def test_resolve_character_creates_entity_record():
    db = FakeDBForResolver()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    result = resolver.resolve_character("Alice", {"description": "Hero"})

    assert result.entity_id is not None
    assert result.universal_id is not None
    assert result.created is True
    assert any(e["name"] == "Alice" and e["entity_type"] == "character" for e in db._entities)


def test_resolve_character_returns_same_ids_on_second_call():
    db = FakeDBForResolver()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    first = resolver.resolve_character("Alice")
    second = resolver.resolve_character("Alice")

    assert first.entity_id == second.entity_id
    assert first.universal_id == second.universal_id
    assert second.created is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/test_resolver.py -v
```

Expected: FAIL — `ResolvedEntity` has no `universal_id` attribute.

- [ ] **Step 3: Update `resolver.py`**

Replace the full file:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipeline.db.client import DBClient


@dataclass
class ResolvedEntity:
    entity_id: str       # type-specific ID (e.g. characters.id)
    universal_id: str    # entities.id — use for relationships / shared_dynamics
    created: bool


class EntityResolver:
    def __init__(self, db: DBClient, novel_id: str, chapter_number: int) -> None:
        self.db = db
        self.novel_id = novel_id
        self.chapter_number = chapter_number
        # (entity_type, lower_name) -> (entity_id, universal_id)
        self._cache: dict[tuple[str, str], tuple[str, str]] = {}

    def resolve_character(self, name: str, metadata: dict[str, Any] | None = None) -> ResolvedEntity:
        return self._resolve("character", name, metadata or {})

    def resolve_location(self, name: str, metadata: dict[str, Any] | None = None) -> ResolvedEntity:
        return self._resolve("location", name, metadata or {})

    def resolve_faction(self, name: str, metadata: dict[str, Any] | None = None) -> ResolvedEntity:
        return self._resolve("faction", name, metadata or {})

    def resolve_object(self, name: str, metadata: dict[str, Any] | None = None) -> ResolvedEntity:
        return self._resolve("object", name, metadata or {})

    def _resolve(self, entity_type: str, name: str, metadata: dict[str, Any]) -> ResolvedEntity:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError(f"Cannot resolve empty {entity_type} name")

        cache_key = (entity_type, normalized_name.lower())
        cached = self._cache.get(cache_key)
        if cached:
            return ResolvedEntity(cached[0], cached[1], created=False)

        table = _table_for(entity_type)
        name_row = self.db.fetchone(
            f"""
            SELECT id, entity_id
            FROM {table}
            WHERE novel_id = %s AND lower(name) = lower(%s)
            LIMIT 1
            """,
            (self.novel_id, normalized_name),
        )
        if name_row:
            entity_id = str(name_row[0])
            universal_id = str(name_row[1]) if name_row[1] else entity_id
            self._cache[cache_key] = (entity_id, universal_id)
            return ResolvedEntity(entity_id, universal_id, created=False)

        if entity_type == "character":
            alias_row = self.db.fetchone(
                """
                SELECT id, entity_id
                FROM characters
                WHERE novel_id = %s
                  AND EXISTS (
                      SELECT 1 FROM unnest(aliases) AS a WHERE lower(a) = lower(%s)
                  )
                LIMIT 1
                """,
                (self.novel_id, normalized_name),
            )
            if alias_row:
                entity_id = str(alias_row[0])
                universal_id = str(alias_row[1]) if alias_row[1] else entity_id
                self._cache[cache_key] = (entity_id, universal_id)
                return ResolvedEntity(entity_id, universal_id, created=False)

        entity_id, universal_id = self._create_entity(entity_type, normalized_name, metadata)
        self._cache[cache_key] = (entity_id, universal_id)
        return ResolvedEntity(entity_id, universal_id, created=True)

    def _create_entity(self, entity_type: str, name: str, metadata: dict[str, Any]) -> tuple[str, str]:
        universal_id = str(self.db.fetchval(
            """
            INSERT INTO entities (novel_id, entity_type, name)
            VALUES (%s, %s, %s)
            ON CONFLICT (novel_id, entity_type, name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (self.novel_id, entity_type, name),
            commit=True,
        ))

        if entity_type == "character":
            entity_id = str(self.db.fetchval(
                """
                INSERT INTO characters (novel_id, entity_id, name, aliases, first_appearance_chapter, description)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.novel_id,
                    universal_id,
                    name,
                    metadata.get("aliases") or [],
                    self.chapter_number,
                    metadata.get("description"),
                ),
                commit=True,
            ))
            return entity_id, universal_id

        if entity_type == "location":
            entity_id = str(self.db.fetchval(
                """
                INSERT INTO locations (novel_id, entity_id, name, description, first_appearance_chapter)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (self.novel_id, universal_id, name, metadata.get("description"), self.chapter_number),
                commit=True,
            ))
            return entity_id, universal_id

        if entity_type == "faction":
            entity_id = str(self.db.fetchval(
                """
                INSERT INTO factions (novel_id, entity_id, name, description)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (self.novel_id, universal_id, name, metadata.get("description")),
                commit=True,
            ))
            return entity_id, universal_id

        if entity_type == "object":
            entity_id = str(self.db.fetchval(
                """
                INSERT INTO objects (novel_id, entity_id, name, description, significance, first_appearance_chapter)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.novel_id,
                    universal_id,
                    name,
                    metadata.get("description"),
                    metadata.get("significance"),
                    self.chapter_number,
                ),
                commit=True,
            ))
            return entity_id, universal_id

        raise ValueError(f"Unsupported entity type: {entity_type}")


def _table_for(entity_type: str) -> str:
    mapping = {
        "character": "characters",
        "location": "locations",
        "faction": "factions",
        "object": "objects",
    }
    if entity_type not in mapping:
        raise ValueError(f"Unsupported entity type: {entity_type}")
    return mapping[entity_type]


__all__ = ["EntityResolver", "ResolvedEntity"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/test_resolver.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extraction/resolver.py backend/tests/test_resolver.py
git commit -m "feat(resolver): populate entities table and expose universal_id"
```

---

## Task 4: Update extraction prompts — new passes, remove relationships from entity_deltas

**Files:**
- Modify: `backend/pipeline/extraction/prompts.py`

- [ ] **Step 1: Update `prompts.py`**

Change `PASS_ORDER` and `PASS_SCHEMAS`. Replace the existing constants block (lines 1–80 approx):

```python
from __future__ import annotations

import json
from textwrap import dedent


PASS_ORDER = [
    "chapter_summary",
    "new_entities",
    "entity_deltas",
    "events",
    "thread_updates",
    "continuity_flags",
    "relationship_updates",
    "dynamics_updates",
]


PASS_SCHEMAS = {
    "chapter_summary": {
        "summary": "string (<= 300 tokens)",
    },
    "new_entities": {
        "characters": [
            {
                "name": "string",
                "aliases": ["string"],
                "description": "string",
            }
        ],
        "locations": [{"name": "string", "description": "string"}],
        "factions": [{"name": "string", "description": "string"}],
        "objects": [
            {
                "name": "string",
                "description": "string",
                "significance": "string",
            }
        ],
    },
    "entity_deltas": {
        "character_deltas": [
            {
                "character_name": "string",
                "location": "string|null",
                "emotional_state": "string|null",
                "goals": "string|null",
                "knowledge": ["string"],
                "physical_state": "string|null",
                "notes": "string|null",
            }
        ]
    },
    "events": {
        "events": [
            {
                "description": "string",
                "event_type": "action|revelation|death|arrival|conflict|other",
                "impact_level": "low|medium|high|critical",
                "involved_characters": ["string"],
                "involved_locations": ["string"],
                "involved_objects": ["string"],
            }
        ]
    },
    "thread_updates": {
        "thread_updates": [
            {
                "title": "string",
                "description": "string",
                "status": "open|progressing|closed",
                "impact": "opens|advances|closes",
                "thread_type": "mystery|conflict|goal|prophecy|secret|other",
                "event_description": "string",
            }
        ]
    },
    "continuity_flags": {
        "continuity_flags": [
            {
                "description": "string",
                "flag_type": "foreshadowing|planted_detail|setup|callback|other",
            }
        ]
    },
    "relationship_updates": {
        "relationship_updates": [
            {
                "entity_a": "string",
                "entity_b": "string",
                "rel_type": "string",
                "from_chapter": "integer|null",
                "to_chapter": "integer|null",
                "notes": "string|null",
            }
        ]
    },
    "dynamics_updates": {
        "dynamics_updates": [
            {
                "entity_a": "string",
                "entity_b": "string",
                "description": "string",
            }
        ]
    },
}
```

Keep the rest of the file (`build_context_block`, `build_system_prompt`, `build_user_prompt`) unchanged.

- [ ] **Step 2: Verify import is clean**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run python -c "from pipeline.extraction.prompts import PASS_ORDER, PASS_SCHEMAS; print(PASS_ORDER)"
```

Expected: `['chapter_summary', 'new_entities', 'entity_deltas', 'events', 'thread_updates', 'continuity_flags', 'relationship_updates', 'dynamics_updates']`

- [ ] **Step 3: Commit**

```bash
git add backend/pipeline/extraction/prompts.py
git commit -m "feat(prompts): add relationship_updates and dynamics_updates passes"
```

---

## Task 5: Update extractor to handle new passes

**Files:**
- Modify: `backend/pipeline/extraction/extractor.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/test_extractor.py` (create if it doesn't exist):

```python
from __future__ import annotations

from pipeline.extraction.extractor import empty_extraction, merge_extractions, _normalize_extraction


def test_empty_extraction_has_relationship_and_dynamics_keys():
    result = empty_extraction()
    assert "relationship_updates" in result
    assert result["relationship_updates"] == []
    assert "dynamics_updates" in result
    assert result["dynamics_updates"] == []


def test_merge_extractions_dedupes_relationship_updates():
    a = empty_extraction()
    a["relationship_updates"] = [
        {"entity_a": "Alice", "entity_b": "Bob", "rel_type": "friend", "from_chapter": 1, "to_chapter": None, "notes": None}
    ]
    b = empty_extraction()
    b["relationship_updates"] = [
        {"entity_a": "Alice", "entity_b": "Bob", "rel_type": "friend", "from_chapter": 1, "to_chapter": None, "notes": None}
    ]
    merged = merge_extractions([a, b])
    assert len(merged["relationship_updates"]) == 1


def test_merge_extractions_dedupes_dynamics_updates():
    a = empty_extraction()
    a["dynamics_updates"] = [
        {"entity_a": "Alice", "entity_b": "Bob", "description": "tense"}
    ]
    b = empty_extraction()
    b["dynamics_updates"] = [
        {"entity_a": "Alice", "entity_b": "Bob", "description": "tense"}
    ]
    merged = merge_extractions([a, b])
    assert len(merged["dynamics_updates"]) == 1


def test_normalize_extraction_passes_through_new_fields():
    raw = {
        "relationship_updates": [
            {"entity_a": "Alice", "entity_b": "Bob", "rel_type": "friend", "from_chapter": 1, "to_chapter": None, "notes": None}
        ],
        "dynamics_updates": [
            {"entity_a": "Alice", "entity_b": "Bob", "description": "warm"}
        ],
    }
    result = _normalize_extraction(raw)
    assert len(result["relationship_updates"]) == 1
    assert len(result["dynamics_updates"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/test_extractor.py -v
```

Expected: FAIL — `empty_extraction` missing keys, etc.

- [ ] **Step 3: Update `extractor.py`**

Update `empty_extraction`:

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
        "entity_deltas": [],
        "events": [],
        "thread_updates": [],
        "continuity_flags": [],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
```

Update `_normalize_extraction` — add handling for new fields at the end of the function (before `return output`):

```python
    relationship_updates = raw.get("relationship_updates", [])
    if isinstance(relationship_updates, list):
        output["relationship_updates"] = [item for item in relationship_updates if isinstance(item, dict)]

    dynamics_updates = raw.get("dynamics_updates", [])
    if isinstance(dynamics_updates, list):
        output["dynamics_updates"] = [item for item in dynamics_updates if isinstance(item, dict)]
```

Update `merge_extractions` — add deduplication for new fields after the `continuity_flags` block:

```python
    seen_relationships: set[tuple[str, str, str]] = set()
    for extraction in extractions:
        for rel in extraction.get("relationship_updates", []):
            a = str(rel.get("entity_a", "")).strip().lower()
            b = str(rel.get("entity_b", "")).strip().lower()
            rel_type = str(rel.get("rel_type", "")).strip().lower()
            if not a or not b:
                continue
            key = (min(a, b), max(a, b), rel_type)
            if key in seen_relationships:
                continue
            seen_relationships.add(key)
            merged["relationship_updates"].append(rel)

    seen_dynamics: set[tuple[str, str]] = set()
    for extraction in extractions:
        for dyn in extraction.get("dynamics_updates", []):
            a = str(dyn.get("entity_a", "")).strip().lower()
            b = str(dyn.get("entity_b", "")).strip().lower()
            if not a or not b:
                continue
            key = (min(a, b), max(a, b))
            if key in seen_dynamics:
                continue
            seen_dynamics.add(key)
            merged["dynamics_updates"].append(dyn)
```

Update `_compose_from_pass_payload` — add new passes to the returned dict:

```python
    def _compose_from_pass_payload(self, pass_payload: dict[str, dict[str, Any]]) -> dict[str, Any]:
        chapter_summary = pass_payload.get("chapter_summary", {})
        new_entities = pass_payload.get("new_entities", {})
        entity_deltas = pass_payload.get("entity_deltas", {})
        events = pass_payload.get("events", {})
        thread_updates = pass_payload.get("thread_updates", {})
        continuity_flags = pass_payload.get("continuity_flags", {})
        relationship_updates = pass_payload.get("relationship_updates", {})
        dynamics_updates = pass_payload.get("dynamics_updates", {})

        return {
            "summary": chapter_summary.get("summary", ""),
            "new_entities": new_entities,
            "entity_deltas": entity_deltas.get("character_deltas", entity_deltas.get("entity_deltas", [])),
            "events": events.get("events", []),
            "thread_updates": thread_updates.get("thread_updates", []),
            "continuity_flags": continuity_flags.get("continuity_flags", []),
            "relationship_updates": relationship_updates.get("relationship_updates", []),
            "dynamics_updates": dynamics_updates.get("dynamics_updates", []),
        }
```

Also update `merge_extractions` delta index — remove the `relationships` key from the delta template:

```python
            if key not in delta_index:
                delta_index[key] = {
                    "character_name": character_name,
                    "location": None,
                    "emotional_state": None,
                    "goals": None,
                    "physical_state": None,
                    "knowledge": [],
                    "notes": None,
                }
```

And remove the relationships merge block inside the delta loop:

```python
            # Remove this block entirely:
            # relationships = delta.get("relationships")
            # if isinstance(relationships, dict):
            #     existing_rels = existing.get("relationships", {})
            #     existing_rels.update(relationships)
            #     existing["relationships"] = existing_rels
```

Also update `_mock_extract` — remove `"relationships": {}` from the delta template it generates (since `entity_deltas` no longer includes relationships):

```python
        for name in candidate_names[:10]:
            extraction["entity_deltas"].append(
                {
                    "character_name": name,
                    "location": None,
                    "emotional_state": None,
                    "goals": None,
                    "knowledge": [],
                    "physical_state": None,
                    "notes": "Generated by mock extraction.",
                }
            )
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/test_extractor.py tests/test_extractor_progress.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extraction/extractor.py backend/tests/test_extractor.py
git commit -m "feat(extractor): handle relationship_updates and dynamics_updates passes"
```

---

## Task 6: Update pipeline.py — persist new passes, remove old relationship/character_state writes

**Files:**
- Modify: `backend/pipeline/pipeline.py`

- [ ] **Step 1: Update `_persist_extraction` in `pipeline.py`**

Replace the `entity_deltas` loop section. The existing loop (around line 210–265) inserts into `character_states` with a `relationships` column and then loops over `relationships.items()` to insert into the `relationships` table. Replace both with:

```python
    for delta in extracted.get("entity_deltas", []):
        character_name = str(delta.get("character_name", "")).strip()
        if not character_name:
            continue
        character_id = resolver.resolve_character(character_name).entity_id

        location_name = str(delta.get("location", "")).strip()
        location_id = None
        if location_name:
            location_id = resolver.resolve_location(location_name).entity_id

        knowledge = delta.get("knowledge")
        if not isinstance(knowledge, list):
            knowledge = []
        knowledge = [str(item) for item in knowledge if str(item).strip()]

        db.execute(
            """
            INSERT INTO character_states (
                character_id,
                chapter_id,
                location_id,
                emotional_state,
                goals,
                knowledge,
                physical_state,
                notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                character_id,
                chapter_id,
                location_id,
                delta.get("emotional_state"),
                delta.get("goals"),
                knowledge,
                delta.get("physical_state"),
                delta.get("notes"),
            ),
        )
```

Then add new persistence blocks after the `entity_deltas` loop, before the `events` loop:

```python
    for rel in extracted.get("relationship_updates", []):
        a_name = str(rel.get("entity_a", "")).strip()
        b_name = str(rel.get("entity_b", "")).strip()
        if not a_name or not b_name:
            continue
        a_universal = resolver.resolve_character(a_name).universal_id
        b_universal = resolver.resolve_character(b_name).universal_id
        db.execute(
            """
            INSERT INTO relationships (
                entity_a_id, entity_b_id, rel_type, from_chapter, to_chapter, notes
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                a_universal,
                b_universal,
                rel.get("rel_type"),
                rel.get("from_chapter"),
                rel.get("to_chapter"),
                rel.get("notes"),
            ),
        )

    for dyn in extracted.get("dynamics_updates", []):
        a_name = str(dyn.get("entity_a", "")).strip()
        b_name = str(dyn.get("entity_b", "")).strip()
        description = str(dyn.get("description", "")).strip()
        if not a_name or not b_name or not description:
            continue
        a_universal = resolver.resolve_character(a_name).universal_id
        b_universal = resolver.resolve_character(b_name).universal_id
        db.execute(
            """
            INSERT INTO shared_dynamics (entity_a_id, entity_b_id, chapter_id, description)
            VALUES (%s, %s, %s, %s)
            """,
            (a_universal, b_universal, chapter_id, description),
        )
```

- [ ] **Step 2: Verify existing pipeline tests still pass**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/ -v --ignore=tests/api
```

Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add backend/pipeline/pipeline.py
git commit -m "feat(pipeline): persist relationship_updates and dynamics_updates; drop relationships JSONB write"
```

---

## Task 7: Update API schemas

**Files:**
- Modify: `backend/api/schemas.py`

- [ ] **Step 1: Update schemas**

Replace `CharacterStateRow`, `CharacterRelationshipRow`, `CharacterDetail` and add `SharedDynamicRow`:

```python
class CharacterStateRow(BaseModel):
    chapter_number: int
    location: str | None
    emotional_state: str | None
    goals: str | None
    knowledge: list[str]
    physical_state: str | None
    notes: str | None


class CharacterRelationshipRow(BaseModel):
    other_entity_id: UUID
    other_entity_name: str
    other_entity_type: str
    direction: str   # "from" = this character is entity_a; "to" = entity_b
    rel_type: str | None
    from_chapter: int | None
    to_chapter: int | None
    notes: str | None


class SharedDynamicRow(BaseModel):
    id: UUID
    entity_a_id: UUID
    entity_b_id: UUID
    chapter_number: int
    description: str | None


class CharacterDetail(BaseModel):
    identity: CharacterSummary
    current_state: CharacterStateRow | None
    history: list[CharacterStateRow]
    relationships: list[CharacterRelationshipRow]
    events: list[CharacterEventRow]
```

- [ ] **Step 2: Verify import**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run python -c "from api.schemas import CharacterRelationshipRow, SharedDynamicRow; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/api/schemas.py
git commit -m "feat(api-schemas): update relationship row, add SharedDynamicRow"
```

---

## Task 8: Update API queries

**Files:**
- Modify: `backend/api/queries.py`
- Modify: `backend/tests/api/conftest.py`
- Modify: `backend/tests/api/test_relationships.py`
- Modify: `backend/tests/api/test_characters.py`

- [ ] **Step 1: Update `conftest.py` FakeDB**

Add `shared_dynamics` and `entities` collections to `FakeDB.__init__`, and update `relationships` field shape:

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
        self.entities = entities or []
```

- [ ] **Step 2: Update `test_relationships.py`** to use new relationship shape (no `entity_a_type`, `entity_b_type`, `status`, `chapter_id`; add `from_chapter`, `to_chapter`):

```python
from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_relationships_graph_returns_nodes_and_edges(fake_db_factory, client):
    novel = make_novel()
    a_entity_id = uuid4()
    b_entity_id = uuid4()
    a_char_id = uuid4()
    b_char_id = uuid4()
    chap1 = make_chapter(novel["id"], 1)
    fake_db_factory(
        novels=[novel],
        chapters=[chap1],
        entities=[
            {"id": a_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Alice"},
            {"id": b_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Bob"},
        ],
        characters=[
            {"id": a_char_id, "entity_id": a_entity_id, "novel_id": novel["id"], "name": "Alice", "aliases": [], "description": None, "first_appearance_chapter": 1},
            {"id": b_char_id, "entity_id": b_entity_id, "novel_id": novel["id"], "name": "Bob", "aliases": [], "description": None, "first_appearance_chapter": 1},
        ],
        relationships=[
            {
                "id": uuid4(),
                "entity_a_id": a_entity_id,
                "entity_b_id": b_entity_id,
                "rel_type": "friend",
                "from_chapter": 1,
                "to_chapter": None,
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

- [ ] **Step 3: Run relationship tests to see current state**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/api/test_relationships.py -v
```

Expected: FAIL (queries don't match new schema yet).

- [ ] **Step 4: Update `queries.py` — `get_character_detail` relationship section**

In `get_character_detail`, find the in-memory path for `rels` and update it to match new shape:

```python
        rels = [
            r
            for r in db.relationships
            if (r["entity_a_id"] == character.get("entity_id") or r["entity_b_id"] == character.get("entity_id"))
        ]
```

Update `state_to_row` to remove the `relationships` field:

```python
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
            "physical_state": state.get("physical_state"),
            "notes": state.get("notes"),
        }
```

Update the real DB path for `rels_rows` query (replaces old query that joined on `chapter_id`):

```python
        rels_rows = db.fetchall(
            """
            SELECT r.id, r.entity_a_id, r.entity_b_id, r.rel_type,
                   r.from_chapter, r.to_chapter, r.notes
            FROM relationships r
            JOIN characters c ON c.entity_id = r.entity_a_id OR c.entity_id = r.entity_b_id
            WHERE c.id = %s
              AND (r.from_chapter IS NULL OR r.from_chapter <= %s)
            """,
            (str(character_id), effective_cap),
            dict_rows=True,
        )
        char_entity_row = db.fetchone(
            "SELECT entity_id FROM characters WHERE id = %s", (str(character_id),)
        )
        char_entity_id = str(char_entity_row[0]) if char_entity_row else None
```

Write a single `rel_to_row` that handles both in-memory and real DB paths (defined after both branches, like the original):

```python
    def rel_to_row(rel: dict[str, Any]) -> dict[str, Any]:
        if hasattr(db, "entities"):
            entity_id_map = {e["id"]: e for e in db.entities}
            this_entity_id = identity.get("entity_id") or next(
                (c.get("entity_id") for c in db.characters if c["id"] == character_id), None
            )
            if rel["entity_a_id"] == this_entity_id:
                other_universal = rel["entity_b_id"]
                direction = "from"
            else:
                other_universal = rel["entity_a_id"]
                direction = "to"
            other_entity = entity_id_map.get(other_universal, {})
            other_name = other_entity.get("name", str(other_universal))
            other_type = other_entity.get("entity_type", "character")
        else:
            if str(rel["entity_a_id"]) == char_entity_id:
                other_universal = rel["entity_b_id"]
                direction = "from"
            else:
                other_universal = rel["entity_a_id"]
                direction = "to"
            other_entity = db.fetchone(
                "SELECT name, entity_type FROM entities WHERE id = %s",
                (str(other_universal),),
                dict_rows=True,
            )
            other_name = other_entity["name"] if other_entity else str(other_universal)
            other_type = other_entity["entity_type"] if other_entity else "character"
        return {
            "other_entity_id": other_universal,
            "other_entity_name": other_name,
            "other_entity_type": other_type,
            "direction": direction,
            "rel_type": rel.get("rel_type"),
            "from_chapter": rel.get("from_chapter"),
            "to_chapter": rel.get("to_chapter"),
            "notes": rel.get("notes"),
        }
```

Also update the real DB path of `get_relationship_graph` — replace the old SQL that filters by `entity_a_type = 'character'`:

```python
        rels_raw = db.fetchall(
            """
            SELECT r.id, r.entity_a_id, r.entity_b_id, r.rel_type, r.from_chapter
            FROM relationships r
            JOIN entities ea ON ea.id = r.entity_a_id AND ea.novel_id = %s AND ea.entity_type = 'character'
            JOIN entities eb ON eb.id = r.entity_b_id AND eb.entity_type = 'character'
            WHERE (r.from_chapter IS NULL OR r.from_chapter <= %s)
            """,
            (str(novel_id), effective_cap),
            dict_rows=True,
        )
        rels = [dict(r) for r in rels_raw]

        def rel_chapter(r: dict[str, Any]) -> int | None:
            return r.get("from_chapter")
```

And update the nodes query in the real DB path:

```python
        characters = [
            dict(r) for r in db.fetchall(
                """
                SELECT e.id, e.name, NULL AS description, c.first_appearance_chapter
                FROM entities e
                JOIN characters c ON c.entity_id = e.id
                WHERE e.novel_id = %s AND e.entity_type = 'character'
                  AND (c.first_appearance_chapter IS NULL OR c.first_appearance_chapter <= %s)
                """,
                (str(novel_id), effective_cap),
                dict_rows=True,
            )
        ]
```

Update `get_relationship_graph` in-memory path to use `entity_id` FK:

```python
        rels = [
            r for r in db.relationships
            if (r["entity_a_id"] in {e["id"] for e in db.entities}
                and r["entity_b_id"] in {e["id"] for e in db.entities})
            and (r.get("from_chapter") is None or r["from_chapter"] <= effective_cap)
        ]
```

Update nodes in `get_relationship_graph` to use `entities` table:

```python
    nodes = [
        {"id": e["id"], "label": e["name"], "description": None}
        for e in (db.entities if hasattr(db, "entities") else [])
        if e.get("novel_id") == novel_id and e.get("entity_type") == "character"
        and any(
            c.get("entity_id") == e["id"] and (c.get("first_appearance_chapter") is None or c["first_appearance_chapter"] <= effective_cap)
            for c in db.characters
        )
    ]
```

- [ ] **Step 5: Run all API tests**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/api/ -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/api/queries.py backend/tests/api/conftest.py backend/tests/api/test_relationships.py backend/tests/api/test_characters.py
git commit -m "feat(api-queries): update queries for new relationships schema and entities table"
```

---

## Task 9: Add shared_dynamics API route

**Files:**
- Create: `backend/api/routes/dynamics.py`
- Modify: `backend/api/app.py`
- Modify: `backend/api/queries.py`
- Create: `backend/tests/api/test_dynamics.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/api/test_dynamics.py`:

```python
from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_list_shared_dynamics_returns_rows(fake_db_factory, client):
    novel = make_novel()
    a_id = uuid4()
    b_id = uuid4()
    chap1 = make_chapter(novel["id"], 1)
    dyn_id = uuid4()
    fake_db_factory(
        novels=[novel],
        chapters=[chap1],
        entities=[
            {"id": a_id, "novel_id": novel["id"], "entity_type": "character", "name": "Alice"},
            {"id": b_id, "novel_id": novel["id"], "entity_type": "character", "name": "Bob"},
        ],
        characters=[],
        shared_dynamics=[
            {
                "id": dyn_id,
                "entity_a_id": a_id,
                "entity_b_id": b_id,
                "chapter_id": chap1["id"],
                "description": "Alice and Bob are tense allies.",
            }
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/dynamics")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["description"] == "Alice and Bob are tense allies."
    assert body[0]["chapter_number"] == 1


def test_list_shared_dynamics_respects_cap(fake_db_factory, client):
    novel = make_novel()
    a_id = uuid4()
    b_id = uuid4()
    chap1 = make_chapter(novel["id"], 1)
    chap5 = make_chapter(novel["id"], 5)
    fake_db_factory(
        novels=[novel],
        chapters=[chap1, chap5],
        entities=[
            {"id": a_id, "novel_id": novel["id"], "entity_type": "character", "name": "Alice"},
            {"id": b_id, "novel_id": novel["id"], "entity_type": "character", "name": "Bob"},
        ],
        characters=[],
        shared_dynamics=[
            {"id": uuid4(), "entity_a_id": a_id, "entity_b_id": b_id, "chapter_id": chap1["id"], "description": "early"},
            {"id": uuid4(), "entity_a_id": a_id, "entity_b_id": b_id, "chapter_id": chap5["id"], "description": "late"},
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/dynamics?cap=3")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["description"] == "early"
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/api/test_dynamics.py -v
```

Expected: FAIL — 404 not found for the route.

- [ ] **Step 3: Add `list_shared_dynamics` to `queries.py`**

```python
def list_shared_dynamics(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "shared_dynamics"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        rows: list[dict[str, Any]] = []
        for dyn in db.shared_dynamics:
            ch = chapter_by_id.get(dyn["chapter_id"])
            if ch is None or ch["number"] > effective_cap:
                continue
            rows.append({
                "id": dyn["id"],
                "entity_a_id": dyn["entity_a_id"],
                "entity_b_id": dyn["entity_b_id"],
                "chapter_number": ch["number"],
                "description": dyn.get("description"),
            })
        rows.sort(key=lambda r: r["chapter_number"])
        return rows

    raw = db.fetchall(
        """
        SELECT sd.id, sd.entity_a_id, sd.entity_b_id, sd.description, ch.number AS chapter_number
        FROM shared_dynamics sd
        JOIN chapters ch ON ch.id = sd.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
        ORDER BY ch.number
        """,
        (str(novel_id), effective_cap),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "entity_a_id": r["entity_a_id"],
            "entity_b_id": r["entity_b_id"],
            "chapter_number": r["chapter_number"],
            "description": r.get("description"),
        }
        for r in raw
    ]
```

- [ ] **Step 4: Create `backend/api/routes/dynamics.py`**

```python
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from api import queries
from api.schemas import SharedDynamicRow

router = APIRouter(prefix="/api/novels/{novel_id}/dynamics", tags=["dynamics"])


@router.get("", response_model=list[SharedDynamicRow])
def list_shared_dynamics(novel_id: UUID, cap: int | None = None):
    return queries.list_shared_dynamics(novel_id, cap)
```

- [ ] **Step 5: Register router in `app.py`**

In `backend/api/app.py`, add to the imports and `include_router` calls:

```python
from api.routes import characters, chapters, continuity, novels, process, relationships, threads, timeline, dynamics
# ...
app.include_router(dynamics.router)
```

- [ ] **Step 6: Run tests**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/api/test_dynamics.py tests/api/ -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add backend/api/routes/dynamics.py backend/api/app.py backend/api/queries.py backend/tests/api/test_dynamics.py
git commit -m "feat(api): add shared_dynamics endpoint GET /novels/{id}/dynamics"
```

---

## Task 10: Run full test suite and verify

- [ ] **Step 1: Run all tests**

```bash
cd /Users/naman/Desktop/gitprojs/continuum/backend
uv run pytest tests/ -v
```

Expected: all PASS with no failures.

- [ ] **Step 2: Verify schema still initialises cleanly**

```bash
cd /Users/naman/Desktop/gitprojs/continuum
uv run novel-webapp init-db
```

Expected: `{"status": "ok", "schema": null}`

- [ ] **Step 3: Final commit if any loose changes remain**

```bash
git add -p
git commit -m "chore: final cleanup after entity relationship refactor"
```
