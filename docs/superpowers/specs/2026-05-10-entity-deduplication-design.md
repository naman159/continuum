# Entity Deduplication — Design Spec

**Date:** 2026-05-10  
**Status:** Approved

## Problem

The pipeline currently creates duplicate entity records when the same entity is
referred to by different name forms within the same extraction (e.g. "Jane" and
"Jane Bennet" in chapter 1). The existing `CharacterCanonicalizer` only compares
extracted names against the DB roster, so it cannot catch duplicates in chapter 1
when the roster is empty. Additionally, it only handles characters — locations,
objects, and factions have no LLM-based dedup at all.

## Solution Overview

Two complementary steps, each making one small focused LLM call per entity type:

1. **`IntraExtractionDeduplicator`** (new) — deduplicates within a single
   extraction, before any DB writes. Catches duplicates even in chapter 1.
2. **`EntityCanonicalizer`** (renamed + generalized from `CharacterCanonicalizer`)
   — matches extracted names against the existing DB roster, for all four entity
   types.

## Entity Types

Both steps handle: `character`, `location`, `object`, `faction`.

---

## Step 1: `IntraExtractionDeduplicator`

### Location

`backend/pipeline/extraction/canonicalizer.py` — new class alongside
`EntityCanonicalizer`.

### When it runs

In `process_chapter` (`pipeline.py`), immediately after `extractor.extract_chapter()`
and before `EntityCanonicalizer.canonicalize()`.

### Inputs

- `extracted: dict[str, Any]` — raw LLM extraction output
- `chapter_text: str` — raw chapter text (passed to LLM for context)

### Output

A rewritten `extracted` dict with all variant names replaced by their canonical
(longest) form. The rest of the pipeline receives this normalized dict unchanged.

### Algorithm (per entity type)

1. Collect all names for the type from `extracted` using `collect_names_by_type`
2. If fewer than 2 names → skip (no LLM call)
3. One LLM call: *"Which of these names refer to the same entity? Return groups."*
4. Within each group: longest name = canonical; shorter forms = variants
5. Rewrite all occurrences of each variant to canonical in `extracted`

### Fields rewritten

All string fields where entity names appear:
- `new_entities.<type>[].name`
- `entity_deltas[].character_name` (characters only)
- `entity_deltas[].location` (locations only)
- `events[].involved_characters[]`, `involved_locations[]`, `involved_objects[]`
- `relationship_updates[].entity_a`, `.entity_b`
- `dynamics_updates[].entity_a`, `.entity_b`

### Helper: `collect_names_by_type`

Replaces `collect_character_names`. Returns `dict[str, set[str]]` mapping entity
type → set of name strings found anywhere in `extracted` for that type.
`collect_character_names` is kept as a thin wrapper for backwards compatibility
with existing callers (just returns `collect_names_by_type(extracted)["character"]`).

### Mock mode

If `use_mock=True` or no LLM available, returns `extracted` unchanged (same
pattern as `EntityCanonicalizer`).

---

## Step 2: `EntityCanonicalizer` (renamed from `CharacterCanonicalizer`)

### Changes from current `CharacterCanonicalizer`

- Renamed to `EntityCanonicalizer`
- `canonicalize()` now loops over all four entity types instead of only characters
- Roster loading is generalized: one DB query per entity type
- One focused LLM call per entity type with unresolved candidates (same
  "1 task per call" principle)
- Alias writes go to the appropriate table for each type

### DB schema change (migration required)

Add `aliases text[] NOT NULL DEFAULT '{}'` to:
- `locations`
- `objects`
- `factions`

Characters already have this column.

### Resolver changes

The alias-lookup query in `EntityResolver._resolve()` currently only runs for
`character` type. It is extended to also run for `location`, `object`, and
`faction`, reading from the appropriate table's `aliases` column.

### `_load_roster` generalisation

Current implementation queries `characters` only. New implementation accepts an
`entity_type` argument and queries the correct table:

| Type | Table | Columns used |
|------|-------|-------------|
| character | characters | id, name, aliases, description |
| location | locations | id, name, aliases, description |
| object | objects | id, name, aliases, description |
| faction | factions | id, name, aliases, description |

---

## Pipeline Order (updated)

```
extract_chapter()
  → IntraExtractionDeduplicator.deduplicate(extracted, chapter_text)   ← new
  → EntityCanonicalizer.canonicalize(chapter_text, candidate_names)    ← extended
  → EntityResolver + _persist_extraction                               ← unchanged
```

---

## Files Changed

| File | Change |
|------|--------|
| `backend/pipeline/extraction/canonicalizer.py` | Add `IntraExtractionDeduplicator`; rename+generalize `CharacterCanonicalizer` → `EntityCanonicalizer`; add `collect_names_by_type`; keep `collect_character_names` as wrapper |
| `backend/pipeline/extraction/prompts.py` | Add prompts for intra-extraction dedup (per type); update canonicalization prompts to be type-agnostic |
| `backend/pipeline/extraction/resolver.py` | Extend alias-lookup to all entity types |
| `backend/pipeline/pipeline.py` | Add `IntraExtractionDeduplicator` pass; update `CharacterCanonicalizer` → `EntityCanonicalizer` import/usage |
| `backend/pipeline/db/schema.sql` | Add `aliases` column to `locations`, `objects`, `factions` |
| `backend/pipeline/db/` | New migration script adding `aliases` to the three tables |
| `backend/tests/test_canonicalizer.py` | Update existing tests for rename; add tests for `IntraExtractionDeduplicator` and generalised `EntityCanonicalizer` |

---

## Out of Scope

- UI for manually merging duplicate entities
- Deduplication of plot threads or continuity flags
- Retroactive dedup of already-processed chapters
