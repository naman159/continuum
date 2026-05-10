# Entity & Relationship Model Refactor

**Date:** 2026-05-10
**Branch:** wiki-webapp

---

## Problem

The current schema conflates two distinct concepts under the `relationships` table:

1. **Structural bonds** — permanent or semi-permanent connections like "husband", "mentor", "sworn enemy"
2. **Relational climate** — how two entities feel about or interact with each other *at a given chapter*

Additionally, `character_states.relationships` JSONB stores per-chapter attitude data that duplicates the second concept, and entity references use a `(entity_id, entity_type)` pair that cannot be enforced as a real foreign key at the DB level.

---

## Goals

- Separate bonds from relational climate into two distinct tables
- Introduce a unified `entities` table so all entity references are proper FK constraints
- Support temporal bonds (a relationship can start and end across chapters)
- Support multiple simultaneous or sequential relationships between the same pair
- Apply consistently to all entity types (characters, locations, factions, objects)

---

## Schema Changes

### New: `entities`

Unified identity table. All entity types register here first.

```sql
CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('character', 'location', 'faction', 'object')),
    name TEXT NOT NULL,
    UNIQUE(novel_id, entity_type, name)
);
```

### Updated: `characters`, `locations`, `factions`, `objects`

Each type table gains an `entity_id` FK column pointing to `entities.id`. Their own `id` and `novel_id` columns remain unchanged.

```sql
ALTER TABLE characters ADD COLUMN entity_id UUID REFERENCES entities(id);
ALTER TABLE locations  ADD COLUMN entity_id UUID REFERENCES entities(id);
ALTER TABLE factions   ADD COLUMN entity_id UUID REFERENCES entities(id);
ALTER TABLE objects    ADD COLUMN entity_id UUID REFERENCES entities(id);
```

### Redesigned: `relationships`

Represents a structural bond between two entities with a temporal range. Two entities can have multiple relationship rows (sequential or overlapping). `to_chapter` being null means the bond is still active.

**Removed columns:** `entity_a_type`, `entity_b_type`, `status`, `chapter_id`

```sql
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
```

`notes` captures context about the bond itself — how it formed, what changed, why it ended.

### New: `shared_dynamics`

A per-chapter snapshot of the relational climate between two entities. Optional — only recorded when something meaningful is happening between them. The `description` is free prose covering stances, tone, tension, asymmetry, and shifts.

```sql
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
```

### Removed: `character_states.relationships JSONB`

Superseded by `shared_dynamics`. This column is dropped after migration.

---

## Migration

1. Create `entities` table
2. Populate `entities` from existing `characters`, `locations`, `factions`, `objects` rows
3. Backfill `entity_id` on each type table
4. Migrate existing `relationships` rows — resolve `entity_a_id`/`entity_b_id` through type tables to `entities.id`, populate `from_chapter` by joining `chapters` on the existing `chapter_id` UUID to get `chapters.number`, set `to_chapter = null`
5. Migrate `character_states.relationships` JSONB — best-effort conversion to `shared_dynamics` rows using chapter context
6. Drop `entity_a_type`, `entity_b_type`, `status`, `chapter_id` from `relationships`
7. Drop `character_states.relationships` column

---

## Pipeline Changes

### Entity creation
When inserting a new character, location, faction, or object — insert into `entities` first, then insert the type-specific row with the returned `entity_id`.

### Extraction schema (`entity_deltas`)
Remove the `relationships` field from `character_deltas`. Relationships and dynamics are now extracted in dedicated passes:

- **`relationship_updates` pass** — extracts bonds between entities: `entity_a`, `entity_b`, `rel_type`, `from_chapter`, `to_chapter`, `notes`
- **`dynamics_updates` pass** — extracts per-pair relational climate: `entity_a`, `entity_b`, `description`

Both passes must be added to `PASS_ORDER` in `pipeline/extraction/prompts.py` and given schemas in `PASS_SCHEMAS`.

### Pipeline inserts
- Use `entity_id` (via lookup through `entities` table) for all relationship and dynamic inserts
- Stop writing to `character_states.relationships`

---

## API Changes

- Update all relationship queries to join through `entities` instead of using `(id, type)` pairs
- Add endpoints for `shared_dynamics` (list by entity pair, list by chapter)
- Update character wiki builder (`wiki/character.py`) to read from `shared_dynamics` instead of `character_states.relationships`
- Update relationship graph builder (`wiki/relationships.py`) similarly

---

## What Does Not Change

- All entity type tables keep their own `id` UUID as primary key
- `character_states` table structure (except dropping the `relationships` JSONB column)
- Novel, chapter, event, thread, continuity flag tables — untouched
