# Entity Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two complementary deduplication passes to the pipeline so that variant entity names (e.g. "Jane" / "Jane Bennet") are merged before DB writes for all four entity types.

**Architecture:** (1) `IntraExtractionDeduplicator` rewrites variant names to canonical within the extracted dict before any DB writes — catches duplicates even in chapter 1. (2) `CharacterCanonicalizer` is renamed `EntityCanonicalizer` and generalised to also handle locations, objects, and factions against the DB roster. Both classes live in `canonicalizer.py` and make one focused LLM call per entity type.

**Tech Stack:** Python, psycopg, litellm, pytest, FastAPI (pipeline only).

---

## File Map

| File | Change |
|------|--------|
| `backend/pipeline/db/schema.sql` | Add `aliases TEXT[] DEFAULT '{}'` to `locations`, `objects`, `factions` |
| `backend/pipeline/db/migrate_add_entity_aliases.py` | Migration script for existing DBs |
| `backend/pipeline/extraction/canonicalizer.py` | Add `IntraExtractionDeduplicator`; add `collect_names_by_type`; keep `collect_character_names` as wrapper; rename+generalise `CharacterCanonicalizer` → `EntityCanonicalizer` |
| `backend/pipeline/extraction/prompts.py` | Add `INTRA_DEDUP_SCHEMA`, `build_intra_dedup_system_prompt`, `build_intra_dedup_user_prompt`; generalise canonicalization prompts to accept entity type |
| `backend/pipeline/extraction/resolver.py` | Extend alias-lookup to all entity types (remove `if entity_type == "character":` guard) |
| `backend/pipeline/pipeline.py` | Add `IntraExtractionDeduplicator` pass; update import to `EntityCanonicalizer`; pass `collect_names_by_type` to canonicalizer |
| `backend/tests/test_canonicalizer.py` | Update existing tests for rename; add tests for `IntraExtractionDeduplicator` and `collect_names_by_type` |
| `backend/tests/test_resolver.py` | Add alias-lookup tests for non-character types |

---

### Task 1: Add aliases columns to locations, objects, factions

**Files:**
- Modify: `backend/pipeline/db/schema.sql`
- Create: `backend/pipeline/db/migrate_add_entity_aliases.py`

- [ ] **Step 1: Update schema.sql**

In `backend/pipeline/db/schema.sql`, add `aliases TEXT[] DEFAULT '{}'` to the `locations`, `factions`, and `objects` table definitions:

```sql
CREATE TABLE IF NOT EXISTS locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    description TEXT,
    aliases TEXT[] DEFAULT '{}',
    parent_location_id UUID REFERENCES locations(id),
    first_appearance_chapter INTEGER,
    UNIQUE(novel_id, name)
);
```

```sql
CREATE TABLE IF NOT EXISTS factions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    description TEXT,
    aliases TEXT[] DEFAULT '{}',
    UNIQUE(novel_id, name)
);
```

```sql
CREATE TABLE IF NOT EXISTS objects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id),
    name TEXT NOT NULL,
    description TEXT,
    significance TEXT,
    aliases TEXT[] DEFAULT '{}',
    first_appearance_chapter INTEGER,
    UNIQUE(novel_id, name)
);
```

- [ ] **Step 2: Create migration script**

Create `backend/pipeline/db/migrate_add_entity_aliases.py`:

```python
from __future__ import annotations

from pipeline.db.client import DBClient


def run() -> None:
    with DBClient() as db:
        db.execute(
            """
            ALTER TABLE locations
                ADD COLUMN IF NOT EXISTS aliases TEXT[] DEFAULT '{}';
            ALTER TABLE factions
                ADD COLUMN IF NOT EXISTS aliases TEXT[] DEFAULT '{}';
            ALTER TABLE objects
                ADD COLUMN IF NOT EXISTS aliases TEXT[] DEFAULT '{}';
            """
        )
    print("Migration complete: aliases columns added to locations, factions, objects.")


if __name__ == "__main__":
    run()
```

- [ ] **Step 3: Commit**

```bash
git add backend/pipeline/db/schema.sql backend/pipeline/db/migrate_add_entity_aliases.py
git commit -m "feat: add aliases columns to locations, factions, objects tables"
```

---

### Task 2: Add `collect_names_by_type` and update `collect_character_names`

**Files:**
- Modify: `backend/pipeline/extraction/canonicalizer.py`
- Test: `backend/tests/test_canonicalizer.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_canonicalizer.py`:

```python
from pipeline.extraction.canonicalizer import collect_names_by_type


def test_collect_names_by_type_characters():
    extracted = {
        "new_entities": {"characters": [{"name": "Jane Bennet"}, {"name": ""}]},
        "entity_deltas": [{"character_name": "Jane", "location": "Netherfield Park"}],
        "events": [
            {
                "involved_characters": ["Jane Bennet"],
                "involved_locations": ["Longbourn"],
                "involved_objects": ["letter"],
            }
        ],
        "relationship_updates": [{"entity_a": "Jane Bennet", "entity_b": "Mr. Bingley"}],
        "dynamics_updates": [{"entity_a": "Jane Bennet", "entity_b": "Mr. Bingley"}],
    }
    by_type = collect_names_by_type(extracted)
    assert by_type["character"] == {"Jane Bennet", "Jane", "Mr. Bingley"}
    assert by_type["location"] == {"Netherfield Park", "Longbourn"}
    assert by_type["object"] == {"letter"}
    assert by_type["faction"] == set()


def test_collect_names_by_type_locations_and_objects():
    extracted = {
        "new_entities": {
            "locations": [{"name": "Pemberley"}],
            "objects": [{"name": "the ring"}],
            "factions": [{"name": "The Order"}],
        },
    }
    by_type = collect_names_by_type(extracted)
    assert by_type["location"] == {"Pemberley"}
    assert by_type["object"] == {"the ring"}
    assert by_type["faction"] == {"The Order"}
    assert by_type["character"] == set()


def test_collect_character_names_is_still_correct():
    extracted = {
        "new_entities": {"characters": [{"name": "Eliza"}]},
        "entity_deltas": [{"character_name": "Mr. Darcy"}],
        "events": [{"involved_characters": ["Eliza"]}],
    }
    # collect_character_names must still work as a wrapper
    from pipeline.extraction.canonicalizer import collect_character_names
    assert collect_character_names(extracted) == {"Eliza", "Mr. Darcy"}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd backend && uv run pytest tests/test_canonicalizer.py::test_collect_names_by_type_characters tests/test_canonicalizer.py::test_collect_names_by_type_locations_and_objects tests/test_canonicalizer.py::test_collect_character_names_is_still_correct -v
```

Expected: ImportError or AttributeError — `collect_names_by_type` not defined yet.

- [ ] **Step 3: Implement `collect_names_by_type`**

In `backend/pipeline/extraction/canonicalizer.py`, add `collect_names_by_type` and update `collect_character_names` to be a wrapper. Replace the existing `collect_character_names` function entirely:

```python
def collect_names_by_type(extracted: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {
        "character": set(),
        "location": set(),
        "object": set(),
        "faction": set(),
    }

    new_entities = extracted.get("new_entities", {}) or {}
    _collect_from_list(new_entities.get("characters"), "character", result)
    _collect_from_list(new_entities.get("locations"), "location", result)
    _collect_from_list(new_entities.get("factions"), "faction", result)
    _collect_from_list(new_entities.get("objects"), "object", result)

    for delta in extracted.get("entity_deltas", []) or []:
        if not isinstance(delta, dict):
            continue
        _add_name(str(delta.get("character_name", "")), "character", result)
        _add_name(str(delta.get("location", "")), "location", result)
        for target in (delta.get("relationships") or {}).keys():
            _add_name(str(target), "character", result)

    for event in extracted.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        for n in event.get("involved_characters", []) or []:
            _add_name(str(n), "character", result)
        for n in event.get("involved_locations", []) or []:
            _add_name(str(n), "location", result)
        for n in event.get("involved_objects", []) or []:
            _add_name(str(n), "object", result)

    for rel in extracted.get("relationship_updates", []) or []:
        if not isinstance(rel, dict):
            continue
        _add_name(str(rel.get("entity_a", "")), "character", result)
        _add_name(str(rel.get("entity_b", "")), "character", result)

    for dyn in extracted.get("dynamics_updates", []) or []:
        if not isinstance(dyn, dict):
            continue
        _add_name(str(dyn.get("entity_a", "")), "character", result)
        _add_name(str(dyn.get("entity_b", "")), "character", result)

    return result


def _collect_from_list(
    items: list[Any] | None,
    entity_type: str,
    result: dict[str, set[str]],
) -> None:
    for item in items or []:
        if isinstance(item, dict):
            _add_name(str(item.get("name", "")), entity_type, result)


def _add_name(name: str, entity_type: str, result: dict[str, set[str]]) -> None:
    normalized = name.strip()
    if normalized:
        result[entity_type].add(normalized)


def collect_character_names(extracted: dict[str, Any]) -> set[str]:
    return collect_names_by_type(extracted)["character"]
```

Also update `__all__` at the bottom:
```python
__all__ = ["CharacterCanonicalizer", "collect_character_names", "collect_names_by_type"]
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/test_canonicalizer.py -v
```

Expected: all existing tests pass + 3 new tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extraction/canonicalizer.py backend/tests/test_canonicalizer.py
git commit -m "feat: add collect_names_by_type; keep collect_character_names as wrapper"
```

---

### Task 3: Add intra-dedup prompts

**Files:**
- Modify: `backend/pipeline/extraction/prompts.py`

- [ ] **Step 1: Add schema and prompt builders**

Append to `backend/pipeline/extraction/prompts.py`:

```python
INTRA_DEDUP_SCHEMA = {
    "groups": [
        {
            "names": ["string (verbatim from input list — only names that refer to the same entity)"],
            "reasoning": "string",
        }
    ]
}


def build_intra_dedup_system_prompt(entity_type: str) -> str:
    return dedent(
        f"""
        You are a strict entity deduplicator for a novel continuity pipeline.
        You will receive a list of {entity_type} names extracted from a chapter.
        Identify groups of names that clearly refer to the SAME {entity_type}
        based solely on the chapter text provided.

        Rules:
        - Only group names when the chapter text makes it unambiguous they are
          the same entity (e.g. "Jane" and "Jane Bennet" used interchangeably,
          "Netherfield" as a clear shorthand for "Netherfield Park").
        - Do NOT group based on general knowledge of the source material.
          Use only the chapter text.
        - Do NOT include singleton groups (groups with only one name).
        - When in doubt, do NOT group. Wrong merges are worse than duplicates.
        - Return ONLY strict JSON matching the schema. No prose, no markdown.

        Output schema:
        {json.dumps(INTRA_DEDUP_SCHEMA, ensure_ascii=True, indent=2)}
        """
    ).strip()


def build_intra_dedup_user_prompt(
    entity_type: str,
    names: list[str],
    chapter_text: str,
) -> str:
    return dedent(
        f"""
        CHAPTER TEXT
        {chapter_text}

        {entity_type.upper()} NAMES TO DEDUPLICATE (JSON)
        {json.dumps(names, ensure_ascii=True)}

        TASK
        Group any names that clearly refer to the same {entity_type}.
        Return only groups of 2 or more. Return JSON only.
        """
    ).strip()
```

- [ ] **Step 2: Commit**

```bash
git add backend/pipeline/extraction/prompts.py
git commit -m "feat: add intra-extraction dedup prompts"
```

---

### Task 4: Implement `IntraExtractionDeduplicator`

**Files:**
- Modify: `backend/pipeline/extraction/canonicalizer.py`
- Test: `backend/tests/test_canonicalizer.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_canonicalizer.py`:

```python
from pipeline.extraction.canonicalizer import IntraExtractionDeduplicator


def _make_deduplicator(payload: dict):
    return IntraExtractionDeduplicator(use_mock=False, completion_fn=_make_completion(payload))


def test_intra_dedup_renames_character_variant():
    extracted = {
        "new_entities": {
            "characters": [{"name": "Jane Bennet", "aliases": [], "description": "eldest Bennet"}, {"name": "Jane", "aliases": [], "description": ""}],
        },
        "entity_deltas": [{"character_name": "Jane", "location": None}],
        "events": [{"involved_characters": ["Jane", "Jane Bennet"], "involved_locations": [], "involved_objects": []}],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
    dedup = _make_deduplicator({"groups": [{"names": ["Jane", "Jane Bennet"], "reasoning": "same person"}]})
    result = dedup.deduplicate(extracted, "Jane walked in. Jane Bennet smiled.")
    chars = [c["name"] for c in result["new_entities"]["characters"]]
    assert "Jane" not in chars
    assert chars.count("Jane Bennet") == 1
    assert result["entity_deltas"][0]["character_name"] == "Jane Bennet"
    assert result["events"][0]["involved_characters"] == ["Jane Bennet", "Jane Bennet"]


def test_intra_dedup_renames_location_variant():
    extracted = {
        "new_entities": {"locations": [{"name": "Netherfield Park"}, {"name": "Netherfield"}]},
        "events": [{"involved_characters": [], "involved_locations": ["Netherfield"], "involved_objects": []}],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
    dedup = _make_deduplicator({"groups": [{"names": ["Netherfield", "Netherfield Park"], "reasoning": "shorthand"}]})
    result = dedup.deduplicate(extracted, "They arrived at Netherfield, also called Netherfield Park.")
    locs = [l["name"] for l in result["new_entities"]["locations"]]
    assert "Netherfield" not in locs
    assert locs.count("Netherfield Park") == 1
    assert result["events"][0]["involved_locations"] == ["Netherfield Park"]


def test_intra_dedup_mock_mode_returns_unchanged():
    extracted = {"new_entities": {"characters": [{"name": "Jane"}, {"name": "Jane Bennet"}]}}
    dedup = IntraExtractionDeduplicator(use_mock=True)
    result = dedup.deduplicate(extracted, "chapter text")
    assert result is not extracted  # deep copy
    assert [c["name"] for c in result["new_entities"]["characters"]] == ["Jane", "Jane Bennet"]


def test_intra_dedup_skips_llm_when_fewer_than_two_names():
    calls: list = []

    def recording_completion(**kwargs):
        calls.append(kwargs)
        return _make_completion({"groups": []})()

    extracted = {"new_entities": {"characters": [{"name": "Jane Bennet"}]}}
    dedup = IntraExtractionDeduplicator(use_mock=False, completion_fn=recording_completion)
    dedup.deduplicate(extracted, "text")
    assert calls == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd backend && uv run pytest tests/test_canonicalizer.py::test_intra_dedup_renames_character_variant tests/test_canonicalizer.py::test_intra_dedup_renames_location_variant tests/test_canonicalizer.py::test_intra_dedup_mock_mode_returns_unchanged tests/test_canonicalizer.py::test_intra_dedup_skips_llm_when_fewer_than_two_names -v
```

Expected: ImportError — `IntraExtractionDeduplicator` not defined yet.

- [ ] **Step 3: Implement `IntraExtractionDeduplicator`**

Add to `backend/pipeline/extraction/canonicalizer.py` after the imports and before `CharacterCanonicalizer`:

```python
import copy

class IntraExtractionDeduplicator:
    """Rewrites variant entity names within a single extraction to their canonical
    (longest) form before any DB writes, using one focused LLM call per entity type."""

    def __init__(
        self,
        *,
        use_mock: bool | None = None,
        completion_fn=None,
    ) -> None:
        self._completion = completion_fn if completion_fn is not None else _load_completion()
        if use_mock is None:
            self.use_mock = settings.use_mock_llm or self._completion is None
        else:
            self.use_mock = use_mock

    def deduplicate(self, extracted: dict[str, Any], chapter_text: str) -> dict[str, Any]:
        result = copy.deepcopy(extracted)
        if self.use_mock:
            return result

        by_type = collect_names_by_type(extracted)
        rename_map: dict[str, dict[str, str]] = {}

        for entity_type, names in by_type.items():
            if len(names) < 2:
                continue
            groups = self._call_llm(entity_type=entity_type, names=sorted(names), chapter_text=chapter_text)
            type_map: dict[str, str] = {}
            for group in groups:
                valid = [n for n in group if isinstance(n, str) and n.strip()]
                if len(valid) < 2:
                    continue
                canonical = max(valid, key=len)
                for variant in valid:
                    if variant != canonical:
                        type_map[variant.lower()] = canonical
            rename_map[entity_type] = type_map

        return _apply_rename_map(result, rename_map)

    def _call_llm(self, *, entity_type: str, names: list[str], chapter_text: str) -> list[list[str]]:
        if self._completion is None:
            return []
        system_prompt = build_intra_dedup_system_prompt(entity_type)
        user_prompt = build_intra_dedup_user_prompt(entity_type, names, chapter_text)
        try:
            response = self._completion(
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
                content = "".join(str(p) for p in content)
            payload = _safe_json_loads(str(content))
        except Exception as exc:
            logger.warning("intra_dedup: LLM call failed: %s", exc)
            return []
        groups_raw = payload.get("groups")
        if not isinstance(groups_raw, list):
            return []
        return [g.get("names", []) for g in groups_raw if isinstance(g, dict)]
```

Then add the `_apply_rename_map` module-level function (after `IntraExtractionDeduplicator`):

```python
def _apply_rename_map(
    extracted: dict[str, Any],
    rename_map: dict[str, dict[str, str]],
) -> dict[str, Any]:
    char_map = rename_map.get("character", {})
    loc_map = rename_map.get("location", {})
    obj_map = rename_map.get("object", {})
    faction_map = rename_map.get("faction", {})

    def _r(name: str, m: dict[str, str]) -> str:
        return m.get(name.lower(), name) if name else name

    new_entities = extracted.get("new_entities", {}) or {}
    for char in new_entities.get("characters", []) or []:
        if isinstance(char, dict):
            char["name"] = _r(char.get("name", ""), char_map)
    for loc in new_entities.get("locations", []) or []:
        if isinstance(loc, dict):
            loc["name"] = _r(loc.get("name", ""), loc_map)
    for faction in new_entities.get("factions", []) or []:
        if isinstance(faction, dict):
            faction["name"] = _r(faction.get("name", ""), faction_map)
    for obj in new_entities.get("objects", []) or []:
        if isinstance(obj, dict):
            obj["name"] = _r(obj.get("name", ""), obj_map)

    # deduplicate new_entities lists by name after renaming
    for key, m in [("characters", char_map), ("locations", loc_map), ("factions", faction_map), ("objects", obj_map)]:
        items = new_entities.get(key) or []
        seen: set[str] = set()
        deduped: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                n = item.get("name", "")
                if n.lower() not in seen:
                    seen.add(n.lower())
                    deduped.append(item)
        if key in new_entities:
            new_entities[key] = deduped

    for delta in extracted.get("entity_deltas", []) or []:
        if not isinstance(delta, dict):
            continue
        delta["character_name"] = _r(delta.get("character_name", ""), char_map)
        if delta.get("location"):
            delta["location"] = _r(delta["location"], loc_map)

    for event in extracted.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        event["involved_characters"] = [_r(n, char_map) for n in (event.get("involved_characters") or [])]
        event["involved_locations"] = [_r(n, loc_map) for n in (event.get("involved_locations") or [])]
        event["involved_objects"] = [_r(n, obj_map) for n in (event.get("involved_objects") or [])]

    for rel in extracted.get("relationship_updates", []) or []:
        if not isinstance(rel, dict):
            continue
        rel["entity_a"] = _r(rel.get("entity_a", ""), char_map)
        rel["entity_b"] = _r(rel.get("entity_b", ""), char_map)

    for dyn in extracted.get("dynamics_updates", []) or []:
        if not isinstance(dyn, dict):
            continue
        dyn["entity_a"] = _r(dyn.get("entity_a", ""), char_map)
        dyn["entity_b"] = _r(dyn.get("entity_b", ""), char_map)

    return extracted
```

Also add the import at the top of `canonicalizer.py`:
```python
from pipeline.extraction.prompts import (
    build_canonicalization_system_prompt,
    build_canonicalization_user_prompt,
    build_intra_dedup_system_prompt,
    build_intra_dedup_user_prompt,
)
```

Update `__all__`:
```python
__all__ = ["CharacterCanonicalizer", "IntraExtractionDeduplicator", "collect_character_names", "collect_names_by_type"]
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/test_canonicalizer.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extraction/canonicalizer.py backend/tests/test_canonicalizer.py
git commit -m "feat: add IntraExtractionDeduplicator"
```

---

### Task 5: Rename `CharacterCanonicalizer` → `EntityCanonicalizer` and generalise

**Files:**
- Modify: `backend/pipeline/extraction/canonicalizer.py`
- Modify: `backend/tests/test_canonicalizer.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_canonicalizer.py`:

```python
from pipeline.extraction.canonicalizer import EntityCanonicalizer


class FakeDBMultiType(FakeDB):
    """Extends FakeDB to support location/object/faction roster rows by type."""

    def __init__(self, roster_by_type: dict[str, list[dict]]) -> None:
        # characters are the default for the parent FakeDB
        super().__init__(roster_by_type.get("character", []))
        self._roster_by_type = roster_by_type

    def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
        for entity_type, rows in self._roster_by_type.items():
            table = {"character": "characters", "location": "locations", "object": "objects", "faction": "factions"}[entity_type]
            if f"FROM {table}" in query:
                return [dict(r) for r in rows]
        return []

    def execute(self, query, params=None):
        self.executed.append((query, tuple(params or ())))
        for entity_type, rows in self._roster_by_type.items():
            table = {"character": "characters", "location": "locations", "object": "objects", "faction": "factions"}[entity_type]
            if f"UPDATE {table}" in query and "aliases = %s" in query:
                new_aliases, row_id, _novel_id = params
                for row in rows:
                    if str(row["id"]) == str(row_id):
                        row["aliases"] = list(new_aliases)


def _make_entity_canon(db, completion_fn):
    return EntityCanonicalizer(
        db,
        novel_id="novel-1",
        use_mock=False,
        completion_fn=completion_fn,
    )


def test_entity_canonicalizer_handles_location_type():
    location_roster = [
        {
            "id": "aaaa0000-0000-0000-0000-000000000001",
            "name": "Netherfield Park",
            "aliases": [],
            "description": "A grand estate.",
        }
    ]
    db = FakeDBMultiType({"location": location_roster})
    response = {
        "resolutions": [
            {
                "candidate": "Netherfield",
                "verdict": "existing",
                "id": "aaaa0000-0000-0000-0000-000000000001",
                "grammatical_anchor": "Netherfield, the great park",
                "reasoning": "shorthand",
            }
        ]
    }
    canon = _make_entity_canon(db, _make_completion(response))
    chapter_text = "They arrived at Netherfield, the great park nearby."
    merges = canon.canonicalize(
        chapter_text=chapter_text,
        candidate_names_by_type={"location": {"Netherfield"}, "character": set(), "object": set(), "faction": set()},
    )
    assert merges.get("location", {}).get("Netherfield") == "aaaa0000-0000-0000-0000-000000000001"
    loc = location_roster[0]
    assert "Netherfield" in loc["aliases"]


def test_entity_canonicalizer_skips_type_with_no_candidates():
    db = FakeDBMultiType({"character": []})
    calls: list = []

    def recording_completion(**kwargs):
        calls.append(kwargs)
        return _make_completion({"resolutions": []})()

    canon = _make_entity_canon(db, recording_completion)
    canon.canonicalize(
        chapter_text="text",
        candidate_names_by_type={"character": set(), "location": set(), "object": set(), "faction": set()},
    )
    assert calls == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd backend && uv run pytest tests/test_canonicalizer.py::test_entity_canonicalizer_handles_location_type tests/test_canonicalizer.py::test_entity_canonicalizer_skips_type_with_no_candidates -v
```

Expected: ImportError — `EntityCanonicalizer` not defined yet.

- [ ] **Step 3: Rename and generalise `CharacterCanonicalizer`**

In `backend/pipeline/extraction/canonicalizer.py`, replace the `CharacterCanonicalizer` class with `EntityCanonicalizer`. Key changes:

```python
class EntityCanonicalizer:
    _TABLE = {
        "character": "characters",
        "location": "locations",
        "object": "objects",
        "faction": "factions",
    }

    def __init__(
        self,
        db: DBClient,
        *,
        novel_id: str,
        use_mock: bool | None = None,
        completion_fn=None,
    ) -> None:
        self.db = db
        self.novel_id = novel_id
        self._completion = completion_fn if completion_fn is not None else _load_completion()
        if use_mock is None:
            self.use_mock = settings.use_mock_llm or self._completion is None
        else:
            self.use_mock = use_mock

    def canonicalize(
        self,
        *,
        chapter_text: str,
        candidate_names_by_type: dict[str, set[str]],
    ) -> dict[str, dict[str, str]]:
        """
        For each entity type, resolves unrecognised candidate names against the
        DB roster and appends matched forms as aliases.

        Returns {entity_type: {candidate_name: entity_id}} for all merges performed.
        """
        if self.use_mock:
            return {}

        all_merges: dict[str, dict[str, str]] = {}
        for entity_type, candidates in candidate_names_by_type.items():
            if not candidates:
                continue
            roster = self._load_roster(entity_type)
            unresolved = self._filter_already_known(candidates, roster)
            if not unresolved or not roster:
                continue
            resolutions = self._call_llm(
                chapter_text=chapter_text,
                entity_type=entity_type,
                candidates=unresolved,
                roster=roster,
            )
            merges = self._apply_resolutions(entity_type, resolutions, roster)
            if merges:
                all_merges[entity_type] = merges

        return all_merges

    def _load_roster(self, entity_type: str) -> list[dict[str, Any]]:
        table = self._TABLE[entity_type]
        if entity_type == "character":
            rows = self.db.fetchall(
                """
                SELECT c.id, c.name, c.aliases, c.description,
                       ls.emotional_state, ls.goals, ls.physical_state
                FROM characters c
                LEFT JOIN LATERAL (
                    SELECT cs.emotional_state, cs.goals, cs.physical_state
                    FROM character_states cs
                    JOIN chapters ch ON ch.id = cs.chapter_id
                    WHERE cs.character_id = c.id
                    ORDER BY ch.number DESC
                    LIMIT 1
                ) ls ON true
                WHERE c.novel_id = %s
                ORDER BY c.name
                """,
                (self.novel_id,),
                dict_rows=True,
            )
            return [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "aliases": list(row.get("aliases") or []),
                    "description": row.get("description"),
                    "last_known": {
                        "emotional_state": row.get("emotional_state"),
                        "goals": row.get("goals"),
                        "physical_state": row.get("physical_state"),
                    },
                }
                for row in rows
            ]

        rows = self.db.fetchall(
            f"""
            SELECT id, name, aliases, description
            FROM {table}
            WHERE novel_id = %s
            ORDER BY name
            """,
            (self.novel_id,),
            dict_rows=True,
        )
        return [
            {
                "id": str(row["id"]),
                "name": row["name"],
                "aliases": list(row.get("aliases") or []),
                "description": row.get("description"),
            }
            for row in rows
        ]

    @staticmethod
    def _filter_already_known(candidates: set[str], roster: list[dict[str, Any]]) -> list[str]:
        known: set[str] = set()
        for entry in roster:
            known.add(str(entry.get("name", "")).lower())
            for alias in entry.get("aliases") or []:
                known.add(str(alias).lower())
        unresolved: list[str] = []
        seen: set[str] = set()
        for name in candidates:
            normalized = (name or "").strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in known or key in seen:
                continue
            seen.add(key)
            unresolved.append(normalized)
        return unresolved

    def _call_llm(
        self,
        *,
        chapter_text: str,
        entity_type: str,
        candidates: list[str],
        roster: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self._completion is None:
            return []
        system_prompt = build_canonicalization_system_prompt(entity_type)
        user_prompt = build_canonicalization_user_prompt(chapter_text, candidates, roster)
        try:
            response = self._completion(
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
                content = "".join(str(p) for p in content)
            payload = _safe_json_loads(str(content))
        except Exception as exc:
            logger.warning("entity_canonicalizer: LLM call failed for %s: %s", entity_type, exc)
            return []
        resolutions = payload.get("resolutions")
        if not isinstance(resolutions, list):
            return []
        return [r for r in resolutions if isinstance(r, dict)]

    def _apply_resolutions(
        self,
        entity_type: str,
        resolutions: list[dict[str, Any]],
        roster: list[dict[str, Any]],
    ) -> dict[str, str]:
        table = self._TABLE[entity_type]
        roster_by_id: dict[str, dict[str, Any]] = {str(item["id"]): item for item in roster}
        merges: dict[str, str] = {}

        for resolution in resolutions:
            candidate = str(resolution.get("candidate", "")).strip()
            if not candidate:
                continue
            if str(resolution.get("verdict", "")).strip().lower() != "existing":
                continue
            target_id = resolution.get("id")
            if not target_id or str(target_id) not in roster_by_id:
                continue
            anchor = str(resolution.get("grammatical_anchor") or "").strip()
            if not anchor or anchor not in chapter_text:
                logger.info(
                    "entity_canonicalizer: rejected merge (anchor missing or not in text): %s -> %s",
                    candidate,
                    target_id,
                )
                continue
            target = roster_by_id[str(target_id)]
            existing_aliases = [str(a) for a in (target.get("aliases") or [])]
            existing_aliases_lower = {a.lower() for a in existing_aliases}
            if candidate.lower() in existing_aliases_lower or candidate.lower() == str(target.get("name", "")).lower():
                merges[candidate] = str(target_id)
                continue
            new_aliases = [*existing_aliases, candidate]
            self.db.execute(
                f"UPDATE {table} SET aliases = %s WHERE id = %s AND novel_id = %s",
                (new_aliases, target_id, self.novel_id),
            )
            target["aliases"] = new_aliases
            merges[candidate] = str(target_id)
            logger.info("entity_canonicalizer: merged %r -> %s (%s)", candidate, target_id, entity_type)

        return merges


# Backwards-compatibility alias
CharacterCanonicalizer = EntityCanonicalizer
```

`_apply_resolutions` requires `chapter_text` for the anchor check. The signature already includes it (shown above). The call site in `canonicalize` must pass it explicitly:

```python
merges = self._apply_resolutions(entity_type, resolutions, roster, chapter_text)
```

Also update `build_canonicalization_system_prompt` in `prompts.py` to accept an `entity_type` parameter. Replace the existing function with:

```python
def build_canonicalization_system_prompt(entity_type: str = "character") -> str:
    return dedent(
        f"""
        You are a strict {entity_type} canonicalizer for a novel continuity pipeline.
        For each candidate name, decide whether it refers to an EXISTING {entity_type}
        in the roster or is a NEW {entity_type}.

        Rules:
        - Verdict "existing" requires BOTH an id from the roster AND a
          grammatical_anchor: a verbatim substring of the chapter text in which
          the candidate is grammatically tied to that existing {entity_type} via
          apposition, unambiguous possessive, or a restated full name in
          immediate context.
        - Do NOT merge based on stylistic similarity, topical inference, plot
          guesswork, or general knowledge of the source novel. Use only the
          chapter text provided.
        - When grammatical evidence is absent, return "new". When in doubt,
          return "new". Visible duplicates are acceptable; wrong merges are not.
        - "grammatical_anchor" must be an EXACT substring of the chapter text.
          If you cannot quote one verbatim, return "new".
        - Return ONLY strict JSON matching the schema. No prose, no markdown.

        Output schema:
        {json.dumps(CANONICALIZATION_SCHEMA, ensure_ascii=True, indent=2)}
        """
    ).strip()
```

Update `__all__` in `canonicalizer.py`:
```python
__all__ = [
    "CharacterCanonicalizer",  # backwards compat alias
    "EntityCanonicalizer",
    "IntraExtractionDeduplicator",
    "collect_character_names",
    "collect_names_by_type",
]
```

- [ ] **Step 4: Update existing canonicalizer tests to use new signature**

In `backend/tests/test_canonicalizer.py`, update `_make_canon` and all `canon.canonicalize(...)` calls to use the new `candidate_names_by_type` parameter:

```python
def _make_canon(db, completion_fn):
    return EntityCanonicalizer(
        db,
        novel_id="novel-1",
        use_mock=False,
        completion_fn=completion_fn,
    )
```

And update each `canon.canonicalize(chapter_text=..., candidate_names=...)` call to:
```python
canon.canonicalize(
    chapter_text=CHAPTER_TEXT,
    candidate_names_by_type={"character": {"the master of Pemberley"}, "location": set(), "object": set(), "faction": set()},
)
```

(Apply to all existing test functions that call `canonicalize`.)

- [ ] **Step 5: Run all canonicalizer tests**

```bash
cd backend && uv run pytest tests/test_canonicalizer.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/extraction/canonicalizer.py backend/pipeline/extraction/prompts.py backend/tests/test_canonicalizer.py
git commit -m "feat: rename CharacterCanonicalizer -> EntityCanonicalizer, generalise to all entity types"
```

---

### Task 6: Extend resolver alias-lookup to all entity types

**Files:**
- Modify: `backend/pipeline/extraction/resolver.py`
- Test: `backend/tests/test_resolver.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_resolver.py` (read the file first to understand existing test setup):

```python
def test_resolve_location_by_alias_returns_existing(tmp_path):
    """Resolver finds a location by alias instead of creating a new one."""
    import uuid
    from pipeline.extraction.resolver import EntityResolver

    class AliasDB:
        def __init__(self):
            self.loc_id = str(uuid.uuid4())
            self.entity_id = str(uuid.uuid4())
            self.executed = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            # Exact name lookup — no match for alias "Netherfield"
            if "lower(name) = lower" in query and "Netherfield Park" not in str(params):
                return None
            # Alias lookup — match when aliases contain "Netherfield"
            if "unnest(aliases)" in query:
                return (self.loc_id, self.entity_id)
            return None

        def fetchval(self, query, params=None, *, commit=False):
            return None

        def execute(self, query, params=None):
            self.executed.append((query, params))

    db = AliasDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    resolved = resolver.resolve_location("Netherfield")
    assert resolved.entity_id == db.loc_id
    assert resolved.created is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd backend && uv run pytest tests/test_resolver.py::test_resolve_location_by_alias_returns_existing -v
```

Expected: FAIL — the resolver currently skips alias lookup for non-character types.

- [ ] **Step 3: Remove the `if entity_type == "character":` guard**

In `backend/pipeline/extraction/resolver.py`, the alias-lookup block currently reads:

```python
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
                ...
```

Replace it with a type-agnostic version that runs for all entity types:

```python
        table = _table_for(entity_type)
        alias_row = self.db.fetchone(
            f"""
            SELECT id, entity_id
            FROM {table}
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
```

Note: the `table` variable is already computed earlier in `_resolve` for the exact-name lookup, so you can remove the duplicate `table = _table_for(entity_type)` line if it's already set.

- [ ] **Step 4: Run all resolver tests**

```bash
cd backend && uv run pytest tests/test_resolver.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extraction/resolver.py backend/tests/test_resolver.py
git commit -m "feat: extend resolver alias-lookup to all entity types"
```

---

### Task 7: Wire pipeline

**Files:**
- Modify: `backend/pipeline/pipeline.py`

- [ ] **Step 1: Update imports**

In `backend/pipeline/pipeline.py`, update the canonicalizer import line:

```python
from pipeline.extraction.canonicalizer import (
    EntityCanonicalizer,
    IntraExtractionDeduplicator,
    collect_names_by_type,
)
```

Remove `CharacterCanonicalizer` and `collect_character_names` from the import (they are no longer used directly).

- [ ] **Step 2: Add the dedup pass and update canonicalization call**

In `process_chapter`, replace the canonicalization block:

```python
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

With:

```python
        if progress is not None:
            progress.on_pass_start("intra_dedup")
        deduplicator = IntraExtractionDeduplicator(use_mock=use_mock_llm)
        extracted = deduplicator.deduplicate(extracted, raw_text)
        if progress is not None:
            progress.on_pass_done("intra_dedup")

        if progress is not None:
            progress.on_pass_start("canonicalization")
        canonicalizer = EntityCanonicalizer(db, novel_id=novel_id, use_mock=use_mock_llm)
        canonicalizer.canonicalize(
            chapter_text=raw_text,
            candidate_names_by_type=collect_names_by_type(extracted),
        )
        if progress is not None:
            progress.on_pass_done("canonicalization")
```

- [ ] **Step 3: Run full test suite**

```bash
cd backend && uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/pipeline/pipeline.py
git commit -m "feat: wire IntraExtractionDeduplicator and EntityCanonicalizer into pipeline"
```
