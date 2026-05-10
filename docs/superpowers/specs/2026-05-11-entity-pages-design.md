# Entity Pages (Locations, Objects, Factions) — Design Spec

**Date:** 2026-05-11  
**Status:** Approved

## Problem

Locations, objects, and factions are tracked in the DB and pipeline but have no API endpoints or UI pages. Users can only see characters.

## Solution

Add list + detail pages for all three entity types, following the existing Characters pattern exactly.

---

## Backend

### New Pydantic schemas (`backend/api/schemas.py`)

```python
LocationSummary: id, name, aliases, description, first_appearance_chapter
LocationDetail: identity: LocationSummary, events: list[TimelineEvent], characters: list[str]

ObjectSummary: id, name, aliases, description, significance, first_appearance_chapter
ObjectRelationship: character_name: str, rel_type: str | None, from_chapter: int | None, to_chapter: int | None, notes: str | None
ObjectDetail: identity: ObjectSummary, events: list[TimelineEvent], characters: list[str], relationships: list[ObjectRelationship]

FactionSummary: id, name, aliases, description
FactionDetail: identity: FactionSummary, events: list[TimelineEvent], characters: list[str]
```

`events` reuses the existing `TimelineEvent` schema. `characters` is a list of character name strings.

### New query functions (`backend/api/queries.py`)

| Function | Description |
|----------|-------------|
| `list_locations(novel_id, cap)` | All locations with `first_appearance_chapter <= cap` (cap=None → all) |
| `get_location_detail(novel_id, location_id, cap)` | Location + events from `involved_locations` array + distinct character names from `character_states.location_id` |
| `list_objects(novel_id, cap)` | All objects with `first_appearance_chapter <= cap` (cap=None → all) |
| `get_object_detail(novel_id, object_id, cap)` | Object + events from `involved_objects` array + distinct character names from those events' `involved_characters` + character→object relationships from `relationships` table (joining via `objects.entity_id`) |
| `list_factions(novel_id)` | All factions (no cap — no `first_appearance_chapter` column) |
| `get_faction_detail(novel_id, faction_id)` | Faction basic info only; events=[], characters=[] |

**Related characters sourcing:**
- Locations: `character_states.location_id = location.id`, joined to chapters for cap filter, distinct character names
- Objects: events where `object.id = ANY(events.involved_objects)`, then collect all `involved_characters` from those events, look up names
- Factions: empty (no membership or event tracking in the data model)

### New API route files

- `backend/api/routes/locations.py` — `GET /api/novels/{novel_id}/locations`, `GET /api/novels/{novel_id}/locations/{location_id}`
- `backend/api/routes/objects.py` — `GET /api/novels/{novel_id}/objects`, `GET /api/novels/{novel_id}/objects/{object_id}`
- `backend/api/routes/factions.py` — `GET /api/novels/{novel_id}/factions`, `GET /api/novels/{novel_id}/factions/{faction_id}`

All three registered in `backend/api/app.py`.

---

## Frontend

### New TypeScript types (added to `frontend/src/api.ts`)

```typescript
LocationSummary: { id, name, aliases, description, first_appearance_chapter }
LocationDetail: { identity: LocationSummary, events: TimelineEvent[], characters: string[] }
ObjectSummary: { id, name, aliases, description, significance, first_appearance_chapter }
ObjectRelationship: { character_name: string, rel_type: string | null, from_chapter: number | null, to_chapter: number | null, notes: string | null }
ObjectDetail: { identity: ObjectSummary, events: TimelineEvent[], characters: string[], relationships: ObjectRelationship[] }
FactionSummary: { id, name, aliases, description }
FactionDetail: { identity: FactionSummary, events: TimelineEvent[], characters: string[] }
```

New api functions: `locations(novelId, cap)`, `location(novelId, locationId, cap)`, `objects(novelId, cap)`, `object(novelId, objectId, cap)`, `factions(novelId)`, `faction(novelId, factionId)`.

### New route files (`frontend/src/routes/`)

| File | Path | Content |
|------|------|---------|
| `Locations.tsx` | `/novels/:novelId/locations` | Table: name (link), aliases, description, first seen. Respects chapter cap. |
| `LocationDetail.tsx` | `/novels/:novelId/locations/:locationId` | Location info + events table + characters list |
| `Objects.tsx` | `/novels/:novelId/objects` | Table: name (link), aliases, description, significance, first seen. Respects chapter cap. |
| `ObjectDetail.tsx` | `/novels/:novelId/objects/:objectId` | Object info + events table + characters list + relationships table (character, type, chapter range, notes) |
| `Factions.tsx` | `/novels/:novelId/factions` | Table: name (link), aliases, description. No chapter cap. |
| `FactionDetail.tsx` | `/novels/:novelId/factions/:factionId` | Faction info only (no events/characters) |

### Changes to existing files

**`frontend/src/App.tsx`:** Add 6 new `<Route>` entries and imports.

**`frontend/src/components/Sidebar.tsx`:** Add Locations, Objects, Factions links between Relationships and Continuity.

---

---

## Pipeline Fix: Cross-Entity Relationships

### Problem

`_persist_extraction` in `pipeline.py` currently calls `resolver.resolve_character()` for **both** sides of every `relationship_update`. If `entity_b` is an object (e.g. "the One Ring"), it gets resolved as a character — creating a duplicate character record or matching nothing. Character→object relationships are never correctly stored.

### Fix

Add `resolve_any_entity(name) -> str` to `EntityResolver` in `resolver.py`. It returns the `entities.id` (universal ID) for any entity type:

1. Check the in-memory cache (keyed as `("any", lower_name)`)
2. Query `entities WHERE novel_id = %s AND lower(name) = lower(%s) LIMIT 1`
3. If found, cache and return `entities.id`
4. If not found, fall back to `resolver.resolve_character(name).universal_id` (preserves current behaviour for pure character-to-character rels)

In `_persist_extraction`, replace the two `resolve_character` calls in the `relationship_updates` loop with `resolve_any_entity`:

```python
a_universal = resolver.resolve_any_entity(a_name)
b_universal = resolver.resolve_any_entity(b_name)
```

Same fix for `dynamics_updates` (lines 338–339), since dynamics can also involve non-character entities.

### Result

Character→object (and character→location, character→faction) relationships are stored with the correct universal IDs, making the ownership section on `ObjectDetail` accurate.

---

## Out of Scope

- Editing or deleting entities
- Faction membership / character-faction relationship views
- Map/graph view for locations
