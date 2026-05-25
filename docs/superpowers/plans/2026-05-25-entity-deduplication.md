# Entity Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent duplicate entity rows across all four entity types (characters, locations, objects, factions) by adding type-specific fingerprinting at extraction time, relaxing canonicalization strictness for non-character types, and folding sub-locations into their parents at resolution time.

**Architecture:** Three layers are touched in sequence — (1) the `new_entities` extraction schema and task instruction gain richer fields (`parent_location` for locations, `owner_name` + `distinguishing_properties` for objects); (2) the intra-extraction deduplicator and cross-chapter canonicalizer get type-specific prompts so strictness matches the merge-risk of each type (characters stay strict, everything else allows semantic evidence); (3) `EntityResolver.resolve_location` folds sub-locations into their parent before creating a new row.

**Tech Stack:** Python 3.11, pytest, LiteLLM (mocked in tests), PostgreSQL (no schema changes required).

---

## File Map

| File | Change |
|------|--------|
| `backend/pipeline/extraction/prompts.py` | Tasks 1, 2, 3 — schemas, task instructions, type-specific prompt builders |
| `backend/pipeline/extraction/canonicalizer.py` | Tasks 2, 3 — enriched object intra-dedup, relax anchor check |
| `backend/pipeline/extraction/resolver.py` | Task 4 — parent-location folding |
| `backend/pipeline/extraction/tests/test_canonicalizer.py` | Tasks 2, 3 — new tests |
| `backend/pipeline/extraction/tests/test_resolver.py` | Task 4 — new tests |

---

## Task 1: Richer Extraction Schemas + new_entities Task Instruction

**Files:**
- Modify: `backend/pipeline/extraction/prompts.py`

### Background
`PASS_SCHEMAS["new_entities"]` currently has bare `{"name": "string", "description": "string"}` entries for locations and objects. There is no guidance on when to use a canonical parent name vs. a sub-location, and no way to express ownership. This task adds the fields and updates the task instruction (which already has a stub for `new_entities`).

- [ ] **Step 1: Update PASS_SCHEMAS for locations and objects**

In `prompts.py`, find `PASS_SCHEMAS` and update the `new_entities` entry:

```python
PASS_SCHEMAS = {
    ...
    "new_entities": {
        "characters": [{"name": "string", "aliases": ["string"], "description": "string"}],
        "locations": [
            {
                "name": "string",
                "description": "string",
                "parent_location": "string|null  # canonical parent if this is a sub-location (room/floor/corridor); null for top-level locations",
            }
        ],
        "factions": [{"name": "string", "description": "string"}],
        "objects": [
            {
                "name": "string",
                "description": "string",
                "owner_name": "string|null  # character who owns/carries this object; null if unowned or unknown",
                "distinguishing_properties": "string|null  # brief note distinguishing this from similar objects (colour, markings, magic, etc.)",
                "significance": "string|null",
            }
        ],
    },
    ...
}
```

- [ ] **Step 2: Update PASS_TASK_INSTRUCTIONS["new_entities"]**

The key `new_entities` already exists in `PASS_TASK_INSTRUCTIONS`. Replace the full entry:

```python
"new_entities": dedent(
    """
    Extract ONLY entities that are genuinely new — not already present in the
    STORY CONTEXT above.

    LOCATIONS
    - Use the canonical, top-level name for a place (e.g. "Corporate Office",
      "Jake's Apartment"). Never create a sub-location row for a room, floor,
      corridor, or fixture that lives inside an already-established location.
    - If the scene happens inside a sub-location (14th floor, elevator, back room),
      set name to the PARENT location and leave parent_location null.
    - If you must distinguish the sub-location (e.g. a named room important to the
      plot), set name to that specific name AND set parent_location to the canonical
      parent (e.g. parent_location="Corporate Office").
    - Name variants of the same place ("Jake's home", "Jake's flat", "Jake's
      apartment") are the SAME location. Pick one canonical name; do not emit both.
    - If a location already exists in STORY CONTEXT under any alias or close variant,
      do NOT create a new entry.

    OBJECTS
    - Set owner_name to the character who owns, carries, or is specifically
      associated with this object. Two characters can each have "a black sedan" —
      they are DIFFERENT objects; give each one a distinct name that includes the
      owner (e.g. "Jake's black sedan", "Sarah's black sedan").
    - Set distinguishing_properties to any brief descriptor that makes this object
      unique (colour, damage, inscriptions, enchantments, etc.).
    - Generic props with no identity (a glass of water, a chair) should NOT be
      extracted as objects unless they recur or carry narrative significance.

    CHARACTERS / FACTIONS
    - Standard rules: only extract if not already in STORY CONTEXT.

    Return JSON only.
    """
).strip(),
```

- [ ] **Step 3: Verify schemas by running a quick import check**

```bash
cd backend && .venv/bin/python -c "
from pipeline.extraction.prompts import PASS_SCHEMAS, PASS_TASK_INSTRUCTIONS
loc = PASS_SCHEMAS['new_entities']['locations'][0]
obj = PASS_SCHEMAS['new_entities']['objects'][0]
assert 'parent_location' in loc, 'missing parent_location'
assert 'owner_name' in obj, 'missing owner_name'
assert 'distinguishing_properties' in obj, 'missing distinguishing_properties'
assert 'new_entities' in PASS_TASK_INSTRUCTIONS
print('PASS')
"
```

Expected: `PASS`

- [ ] **Step 4: Commit**

```bash
git add backend/pipeline/extraction/prompts.py
git commit -m "feat: add owner_name, distinguishing_properties to objects schema; parent_location to locations schema"
```

---

## Task 2: Type-Specific Intra-Extraction Deduplication

**Files:**
- Modify: `backend/pipeline/extraction/prompts.py`
- Modify: `backend/pipeline/extraction/canonicalizer.py`
- Test: `backend/pipeline/extraction/tests/test_canonicalizer.py`

### Background
`build_intra_dedup_system_prompt(entity_type)` currently returns identical instructions for all types. The rules should differ: characters need strict evidence; locations should fold sub-locations and name variants; objects must NOT merge across different owners; factions can allow abbreviations.

Additionally, objects now have `owner_name` in the extraction. The deduplicator only sends *names* to the LLM — for objects we also need to send owner context so the LLM knows two "black sedan" entries belong to different people.

- [ ] **Step 1: Write failing tests for type-specific intra-dedup behaviour**

In `backend/pipeline/extraction/tests/test_canonicalizer.py`, add at the bottom:

```python
# ---------------------------------------------------------------------------
# Type-specific intra-dedup prompt content
# ---------------------------------------------------------------------------

from pipeline.extraction.prompts import build_intra_dedup_system_prompt, build_intra_dedup_user_prompt


def test_intra_dedup_prompt_character_requires_explicit_evidence():
    prompt = build_intra_dedup_system_prompt("character")
    assert "when in doubt" in prompt.lower() or "do not group" in prompt.lower()


def test_intra_dedup_prompt_location_mentions_sub_location():
    prompt = build_intra_dedup_system_prompt("location")
    assert "sub-location" in prompt.lower() or "parent" in prompt.lower()


def test_intra_dedup_prompt_object_mentions_owner():
    prompt = build_intra_dedup_system_prompt("object")
    assert "owner" in prompt.lower()


def test_intra_dedup_prompt_faction_mentions_abbreviation():
    prompt = build_intra_dedup_system_prompt("faction")
    assert "abbreviation" in prompt.lower() or "alias" in prompt.lower()


def test_intra_dedup_user_prompt_objects_include_owner_context():
    """Object user prompt should include owner_name alongside the entity name."""
    entities = [
        {"name": "black sedan", "owner_name": "Jake"},
        {"name": "black sedan", "owner_name": "Sarah"},
    ]
    prompt = build_intra_dedup_user_prompt("object", entities, "Jake drove his black sedan. Sarah drove hers.")
    assert "Jake" in prompt
    assert "Sarah" in prompt


def test_intra_dedup_user_prompt_non_objects_accept_plain_names():
    """Non-object user prompt still works with a plain list of strings."""
    prompt = build_intra_dedup_user_prompt("character", ["Jane", "Jane Bennet"], "text")
    assert "Jane" in prompt
    assert "Jane Bennet" in prompt


def test_intra_dedup_objects_different_owners_not_merged():
    """Objects with different owners must not be merged even if names match."""
    extracted = {
        "new_entities": {
            "objects": [
                {"name": "black sedan", "owner_name": "Jake", "description": "Jake's car"},
                {"name": "black sedan", "owner_name": "Sarah", "description": "Sarah's car"},
            ],
        },
        "entity_deltas": [],
        "events": [],
        "relationship_updates": [],
        "dynamics_updates": [],
    }
    # LLM returns NO groups (correctly deciding not to merge)
    dedup = _make_deduplicator({"groups": []})
    result = dedup.deduplicate(extracted, "Jake drove his black sedan. Sarah had one too.")
    objs = result["new_entities"]["objects"]
    assert len(objs) == 2, "Different-owner objects must not be merged"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend && .venv/bin/pytest pipeline/extraction/tests/test_canonicalizer.py::test_intra_dedup_prompt_location_mentions_sub_location pipeline/extraction/tests/test_canonicalizer.py::test_intra_dedup_prompt_object_mentions_owner pipeline/extraction/tests/test_canonicalizer.py::test_intra_dedup_user_prompt_objects_include_owner_context -v
```

Expected: FAILED (prompts don't mention sub-location/owner yet)

- [ ] **Step 3: Update `build_intra_dedup_system_prompt` to be type-specific**

In `prompts.py`, replace the existing `build_intra_dedup_system_prompt` function:

```python
_INTRA_DEDUP_RULES: dict[str, str] = {
    "character": dedent(
        """
        Rules:
        - Only group names when the chapter text makes it UNAMBIGUOUS they are the
          same person (e.g. "Jane" and "Jane Bennet" used interchangeably, an
          explicit apposition like "Eliza, that is Miss Bennet").
        - Do NOT group based on general knowledge of the source material.
        - When in doubt, do NOT group. Wrong character merges are catastrophic.
        """
    ).strip(),
    "location": dedent(
        """
        Rules:
        - Group name variants that clearly refer to the same place ("Jake's home"
          and "Jake's apartment", "Netherfield" and "Netherfield Park").
        - Group a sub-location with its parent when the chapter makes clear one is
          inside the other ("14th floor of the corporate office" → "Corporate
          Office", "elevator in the office building" → "Corporate Office"). Use the
          PARENT (more general) name as the canonical form.
        - Semantic similarity is sufficient evidence — you do not need a verbatim
          apposition. Use judgment based on description and context.
        - When in doubt, keep separate rather than merge across distinct buildings.
        """
    ).strip(),
    "object": dedent(
        """
        Rules:
        - NEVER merge two objects that belong to DIFFERENT owners, even if their
          names or descriptions sound identical. Two characters can each own "a
          black sedan" — those are two different cars.
        - DO group objects when the same owner refers to the same item by different
          names ("Jake's sword" and "the blade Jake carries" → same object).
        - If owner information is missing for both candidates, you may group only
          when the chapter text makes identity unambiguous (e.g. a named unique
          artifact referred to two ways).
        - When in doubt, do NOT group.
        """
    ).strip(),
    "faction": dedent(
        """
        Rules:
        - Group a faction with its common abbreviation or alias when the chapter
          clearly equates them ("the Empire" used as shorthand for "the Galactic
          Empire", "HYDRA" and "the HYDRA organisation").
        - Semantic similarity and context are sufficient — no verbatim apposition
          required.
        - When in doubt, keep separate.
        """
    ).strip(),
}


def build_intra_dedup_system_prompt(entity_type: str) -> str:
    rules = _INTRA_DEDUP_RULES.get(entity_type, dedent(
        f"""
        Rules:
        - Only group names when the chapter text makes it unambiguous they refer
          to the same {entity_type}. When in doubt, do NOT group.
        """
    ).strip())
    return dedent(
        f"""
        You are a strict entity deduplicator for a novel continuity pipeline.
        You will receive a list of {entity_type} entities extracted from a chapter.
        Identify groups of entries that clearly refer to the SAME {entity_type}
        based solely on the chapter text provided.

        {rules}

        - Do NOT include singleton groups (groups with only one entry).
        - Return ONLY strict JSON matching the schema. No prose, no markdown.

        Output schema:
        {json.dumps(INTRA_DEDUP_SCHEMA, ensure_ascii=True, indent=2)}
        """
    ).strip()
```

- [ ] **Step 4: Update `build_intra_dedup_user_prompt` to accept enriched object data**

Replace the existing `build_intra_dedup_user_prompt`:

```python
def build_intra_dedup_user_prompt(
    entity_type: str,
    entities: list[str | dict],
    chapter_text: str,
) -> str:
    """
    entities: for objects, a list of dicts with at least {"name": str, "owner_name": str|None};
              for all other types, a list of name strings (backwards-compatible).
    """
    if entity_type == "object":
        enriched = []
        for e in entities:
            if isinstance(e, dict):
                entry: dict = {"name": e.get("name", "")}
                if e.get("owner_name"):
                    entry["owner_name"] = e["owner_name"]
                if e.get("distinguishing_properties"):
                    entry["distinguishing_properties"] = e["distinguishing_properties"]
                enriched.append(entry)
            else:
                enriched.append({"name": str(e)})
        entities_block = json.dumps(enriched, ensure_ascii=True)
        label = "OBJECT ENTITIES TO DEDUPLICATE (JSON — includes owner context)"
    else:
        names = [e if isinstance(e, str) else e.get("name", "") for e in entities]
        entities_block = json.dumps(names, ensure_ascii=True)
        label = f"{entity_type.upper()} NAMES TO DEDUPLICATE (JSON)"

    return dedent(
        f"""
        CHAPTER TEXT
        {chapter_text}

        {label}
        {entities_block}

        TASK
        Group any entries that clearly refer to the same {entity_type}.
        Return only groups of 2 or more. Return JSON only.
        """
    ).strip()
```

- [ ] **Step 5: Update `IntraExtractionDeduplicator` to pass enriched object data**

In `canonicalizer.py`, update the `deduplicate` method. Find this block:

```python
        for entity_type, names in by_type.items():
            if len(names) < 2:
                continue
            groups = self._call_llm(entity_type=entity_type, names=sorted(names), chapter_text=chapter_text)
```

Replace with:

```python
        raw_entities_by_type = _collect_entities_by_type(extracted)

        for entity_type, names in by_type.items():
            if len(names) < 2:
                continue
            if entity_type == "object":
                entities_input = raw_entities_by_type.get("object", [])
            else:
                entities_input = sorted(names)
            groups = self._call_llm(entity_type=entity_type, entities=entities_input, chapter_text=chapter_text)
```

Then update `_call_llm` signature and body in `IntraExtractionDeduplicator`:

```python
    def _call_llm(self, *, entity_type: str, entities: list[str | dict], chapter_text: str) -> list[list[str]]:
        if self._completion is None:
            return []
        system_prompt = build_intra_dedup_system_prompt(entity_type)
        user_prompt = build_intra_dedup_user_prompt(entity_type, entities, chapter_text)
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

Add the `_collect_entities_by_type` helper at module level in `canonicalizer.py`:

```python
def _collect_entities_by_type(extracted: dict[str, Any]) -> dict[str, list[dict]]:
    """Return full entity dicts from new_entities, keyed by entity type."""
    new_entities = extracted.get("new_entities", {}) or {}
    return {
        "character": [e for e in (new_entities.get("characters") or []) if isinstance(e, dict)],
        "location": [e for e in (new_entities.get("locations") or []) if isinstance(e, dict)],
        "faction": [e for e in (new_entities.get("factions") or []) if isinstance(e, dict)],
        "object": [e for e in (new_entities.get("objects") or []) if isinstance(e, dict)],
    }
```

- [ ] **Step 6: Run all intra-dedup tests**

```bash
cd backend && .venv/bin/pytest pipeline/extraction/tests/test_canonicalizer.py -v -k "intra_dedup"
```

Expected: All pass, including the five new tests added in Step 1.

- [ ] **Step 7: Commit**

```bash
git add backend/pipeline/extraction/prompts.py backend/pipeline/extraction/canonicalizer.py backend/pipeline/extraction/tests/test_canonicalizer.py
git commit -m "feat: type-specific intra-dedup prompts; pass owner context for object dedup"
```

---

## Task 3: Type-Specific Cross-Chapter Canonicalization

**Files:**
- Modify: `backend/pipeline/extraction/prompts.py`
- Modify: `backend/pipeline/extraction/canonicalizer.py`
- Test: `backend/pipeline/extraction/tests/test_canonicalizer.py`

### Background
`EntityCanonicalizer` currently requires a verbatim `grammatical_anchor` for ALL entity types before writing an alias. This is correct for characters (wrong merges are catastrophic) but too strict for locations, objects, and factions where semantic similarity is sufficient evidence. This task makes the anchor check conditional on entity type.

The LLM schema also stays the same — `grammatical_anchor` remains in the response schema, but for non-character types the canonicalizer accepts a non-empty `reasoning` in lieu of a verbatim anchor.

- [ ] **Step 1: Write failing tests**

Add to `backend/pipeline/extraction/tests/test_canonicalizer.py`:

```python
# ---------------------------------------------------------------------------
# Type-specific cross-chapter canonicalization (relax anchor for non-chars)
# ---------------------------------------------------------------------------

def test_location_merges_without_anchor(roster_rows):
    """Locations should merge on semantic reasoning even without a verbatim anchor."""
    location_roster = [
        {
            "id": "loc-0001-0000-0000-0000-000000000001",
            "name": "Corporate Office",
            "aliases": [],
            "description": "A glass office building where Jake works.",
        }
    ]
    db = FakeDBMultiType({"location": location_roster})
    response = {
        "resolutions": [
            {
                "candidate": "Jake's office building",
                "verdict": "existing",
                "id": "loc-0001-0000-0000-0000-000000000001",
                "grammatical_anchor": "",   # no verbatim anchor
                "reasoning": "The chapter describes Jake going to his office; 'Jake's office building' is the same building as 'Corporate Office'.",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    merges = canon.canonicalize(
        chapter_text="Jake walked to his office building and swiped his badge.",
        candidate_names_by_type={"character": set(), "location": {"Jake's office building"}, "object": set(), "faction": set()},
    )
    assert merges.get("location", {}).get("Jake's office building") == "loc-0001-0000-0000-0000-000000000001"
    assert "Jake's office building" in location_roster[0]["aliases"]


def test_object_merges_without_anchor():
    """Objects should merge on semantic reasoning without a verbatim anchor."""
    obj_roster = [
        {
            "id": "obj-0001-0000-0000-0000-000000000001",
            "name": "Jake's sword",
            "aliases": [],
            "description": "A katana owned by Jake.",
        }
    ]
    db = FakeDBMultiType({"object": obj_roster})
    response = {
        "resolutions": [
            {
                "candidate": "the blade",
                "verdict": "existing",
                "id": "obj-0001-0000-0000-0000-000000000001",
                "grammatical_anchor": "",
                "reasoning": "Jake wields 'the blade' in this chapter; he only has one sword in the roster.",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    merges = canon.canonicalize(
        chapter_text="Jake raised the blade and cut through the air.",
        candidate_names_by_type={"character": set(), "location": set(), "object": {"the blade"}, "faction": set()},
    )
    assert merges.get("object", {}).get("the blade") == "obj-0001-0000-0000-0000-000000000001"


def test_faction_merges_without_anchor():
    """Factions should merge on semantic reasoning without a verbatim anchor."""
    faction_roster = [
        {
            "id": "fac-0001-0000-0000-0000-000000000001",
            "name": "The Galactic Empire",
            "aliases": [],
            "description": "Authoritarian ruling faction.",
        }
    ]
    db = FakeDBMultiType({"faction": faction_roster})
    response = {
        "resolutions": [
            {
                "candidate": "the Empire",
                "verdict": "existing",
                "id": "fac-0001-0000-0000-0000-000000000001",
                "grammatical_anchor": "",
                "reasoning": "The Empire is the common shorthand for The Galactic Empire used throughout.",
            }
        ]
    }
    canon = EntityCanonicalizer(db, novel_id="novel-1", use_mock=False, completion_fn=_make_completion(response))
    merges = canon.canonicalize(
        chapter_text="The Empire tightened its grip on the outer systems.",
        candidate_names_by_type={"character": set(), "location": set(), "object": set(), "faction": {"the Empire"}},
    )
    assert merges.get("faction", {}).get("the Empire") == "fac-0001-0000-0000-0000-000000000001"


def test_character_still_requires_anchor_when_absent(roster_rows):
    """Characters must still be rejected when no grammatical anchor is provided."""
    db = FakeDB(roster_rows)
    response = {
        "resolutions": [
            {
                "candidate": "the tall man",
                "verdict": "existing",
                "id": "11111111-1111-1111-1111-111111111111",
                "grammatical_anchor": "",
                "reasoning": "Probably Darcy because he is tall.",
            }
        ]
    }
    canon = _make_canon(db, _make_completion(response))
    merges = canon.canonicalize(
        chapter_text="A tall man entered the room.",
        candidate_names_by_type={"character": {"the tall man"}, "location": set(), "object": set(), "faction": set()},
    )
    assert merges == {}


def test_canonicalization_system_prompt_character_mentions_anchor():
    from pipeline.extraction.prompts import build_canonicalization_system_prompt
    prompt = build_canonicalization_system_prompt("character")
    assert "grammatical_anchor" in prompt or "anchor" in prompt.lower()


def test_canonicalization_system_prompt_location_relaxed():
    from pipeline.extraction.prompts import build_canonicalization_system_prompt
    prompt = build_canonicalization_system_prompt("location")
    # Should not require anchor — should mention reasoning or semantic
    assert "reasoning" in prompt.lower() or "semantic" in prompt.lower()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend && .venv/bin/pytest pipeline/extraction/tests/test_canonicalizer.py::test_location_merges_without_anchor pipeline/extraction/tests/test_canonicalizer.py::test_character_still_requires_anchor_when_absent pipeline/extraction/tests/test_canonicalizer.py::test_canonicalization_system_prompt_location_relaxed -v
```

Expected: FAILED

- [ ] **Step 3: Add type-specific canonicalization system prompts in `prompts.py`**

Replace the existing `build_canonicalization_system_prompt` function:

```python
_CANON_RULES_BY_TYPE: dict[str, str] = {
    "character": dedent(
        """
        Rules:
        - Verdict "existing" requires BOTH an id from the roster AND a
          grammatical_anchor: a verbatim substring of the chapter text in which
          the candidate is grammatically tied to that existing character via
          apposition, unambiguous possessive, or a restated full name in
          immediate context.
        - Do NOT merge based on stylistic similarity, topical inference, plot
          guesswork, or general knowledge of the source novel. Use only the
          chapter text provided.
        - When grammatical evidence is absent, return "new". When in doubt,
          return "new". Visible duplicates are acceptable; wrong character
          merges destroy continuity.
        - "grammatical_anchor" must be an EXACT substring of the chapter text.
          If you cannot quote one verbatim, return "new".
        """
    ).strip(),
    "location": dedent(
        """
        Rules:
        - Verdict "existing" requires an id from the roster AND clear reasoning
          that the candidate refers to the same physical place.
        - A verbatim grammatical_anchor is NOT required for locations. Semantic
          evidence is sufficient: the chapter describes the same building, the
          candidate name is a common variant or sub-location of an existing entry
          (e.g. "14th floor" → "Corporate Office", "Jake's home" → "Jake's
          Apartment"), or context makes the identity clear.
        - Still set grammatical_anchor to a relevant quote if one exists, else "".
        - When a candidate is clearly a sub-location (floor, room, corridor) of an
          existing roster entry, return verdict "existing" for that parent.
        - Return "new" only for genuinely distinct, named places not in the roster.
        """
    ).strip(),
    "object": dedent(
        """
        Rules:
        - Verdict "existing" requires an id from the roster AND reasoning that the
          candidate is the same physical object.
        - A verbatim grammatical_anchor is NOT required. Description similarity,
          ownership context, and narrative continuity are sufficient evidence.
        - CRITICAL: NEVER merge two objects that belong to different owners. If the
          roster entry belongs to Jake and the candidate belongs to Sarah, return
          "new" even if the names are identical.
        - A named unique item (a specific sword, a magical artifact) referred to by
          two names in the same owner context SHOULD be merged.
        - When in doubt, return "new" — duplicate objects are less harmful than
          wrong merges.
        """
    ).strip(),
    "faction": dedent(
        """
        Rules:
        - Verdict "existing" requires an id from the roster AND clear reasoning
          that the candidate is the same organisation.
        - A verbatim grammatical_anchor is NOT required. Abbreviations, aliases,
          and common shorthand are sufficient evidence when the context makes the
          identity unambiguous.
        - Still set grammatical_anchor to a relevant quote if one exists, else "".
        - Return "new" only for organisations clearly distinct from those in the
          roster.
        """
    ).strip(),
}


def build_canonicalization_system_prompt(entity_type: str = "character") -> str:
    rules = _CANON_RULES_BY_TYPE.get(entity_type, _CANON_RULES_BY_TYPE["character"])
    return dedent(
        f"""
        You are a strict {entity_type} canonicalizer for a novel continuity pipeline.
        For each candidate name, decide whether it refers to an EXISTING {entity_type}
        in the roster or is a NEW {entity_type}.

        {rules}

        - Return ONLY strict JSON matching the schema. No prose, no markdown.

        Output schema:
        {json.dumps(CANONICALIZATION_SCHEMA, ensure_ascii=True, indent=2)}
        """
    ).strip()
```

- [ ] **Step 4: Relax anchor check in `EntityCanonicalizer._apply_resolutions`**

In `canonicalizer.py`, find `_apply_resolutions`. The anchor-rejection block is:

```python
            anchor = str(resolution.get("grammatical_anchor") or "").strip()
            if not anchor or anchor not in chapter_text:
                logger.info(
                    "entity_canonicalizer: rejected merge (anchor missing or not in text): %s -> %s",
                    candidate,
                    target_id,
                )
                continue
```

Replace it with:

```python
            anchor = str(resolution.get("grammatical_anchor") or "").strip()
            reasoning = str(resolution.get("reasoning") or "").strip()

            # Characters always require a verbatim anchor in the chapter text.
            # Other types accept a non-empty reasoning string as sufficient evidence.
            if entity_type == "character":
                if not anchor or anchor not in chapter_text:
                    logger.info(
                        "entity_canonicalizer: rejected character merge (anchor missing or not in text): %s -> %s",
                        candidate,
                        target_id,
                    )
                    continue
            else:
                # For non-character types: accept if either a valid anchor OR reasoning exists.
                anchor_valid = bool(anchor) and anchor in chapter_text
                has_reasoning = bool(reasoning)
                if not anchor_valid and not has_reasoning:
                    logger.info(
                        "entity_canonicalizer: rejected %s merge (no anchor and no reasoning): %s -> %s",
                        entity_type,
                        candidate,
                        target_id,
                    )
                    continue
```

Note: `entity_type` is the first parameter of `_apply_resolutions`. Confirm its signature is:
```python
def _apply_resolutions(self, entity_type: str, resolutions, roster, chapter_text) -> dict[str, str]:
```
It already receives `entity_type` as the first argument — no signature change needed.

- [ ] **Step 5: Run all canonicalizer tests**

```bash
cd backend && .venv/bin/pytest pipeline/extraction/tests/test_canonicalizer.py -v
```

Expected: All pass (including the six new tests from Step 1 plus all existing tests).

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/extraction/prompts.py backend/pipeline/extraction/canonicalizer.py backend/pipeline/extraction/tests/test_canonicalizer.py
git commit -m "feat: type-specific canonicalization — strict anchor for characters, semantic reasoning for locations/objects/factions"
```

---

## Task 4: Parent-Location Folding in Resolver

**Files:**
- Modify: `backend/pipeline/extraction/resolver.py`
- Test: `backend/pipeline/extraction/tests/test_resolver.py`

### Background
Even with the improved extraction prompt and canonicalization, a sub-location that slips through (e.g. `parent_location = "Corporate Office"` set by the LLM at extraction time) should be silently folded into its parent at resolution time. `resolve_location` receives the full entity dict as `metadata`; if `parent_location` is set there, resolve the parent instead.

- [ ] **Step 1: Write failing tests**

Add to `backend/pipeline/extraction/tests/test_resolver.py`:

```python
# ---------------------------------------------------------------------------
# Parent-location folding
# ---------------------------------------------------------------------------

def test_resolve_location_folds_into_parent_when_parent_location_set():
    """
    When metadata contains parent_location and that parent exists in the DB,
    resolve_location should return the parent's entity rather than creating a
    new sub-location row.
    """
    import uuid

    parent_loc_id = str(uuid.uuid4())
    parent_entity_id = str(uuid.uuid4())

    class ParentDB:
        def __init__(self):
            self.executed = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            # Exact name lookup for "Corporate Office" → return parent row
            if "lower(name) = lower" in query and params and str(params[1]).lower() == "corporate office":
                return (parent_loc_id, parent_entity_id)
            # Any other lookup → not found
            return None

        def fetchval(self, query, params=None, *, commit=False):
            return None

        def execute(self, query, params=None):
            self.executed.append((query, params))

    db = ParentDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=5)
    result = resolver.resolve_location(
        "Corporate office (14th floor)",
        {"parent_location": "Corporate Office", "description": "The 14th floor."},
    )
    assert result.entity_id == parent_loc_id
    assert result.universal_id == parent_entity_id
    assert result.created is False
    # The sub-location name must NOT have been inserted
    assert not any("14th floor" in str(params) for _, params in db.executed if params)


def test_resolve_location_creates_parent_if_missing():
    """
    When metadata has parent_location but the parent doesn't exist yet,
    resolve_location creates the parent (not the sub-location).
    """
    import uuid

    class MissingParentDB:
        def __init__(self):
            self.inserted_names: list[str] = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            return None  # nothing exists yet

        def fetchval(self, query, params=None, *, commit=False):
            new_id = uuid.uuid4()
            if params and len(params) >= 3:
                self.inserted_names.append(str(params[2]))  # name arg position in INSERT INTO entities
            return new_id

        def execute(self, query, params=None):
            pass

    db = MissingParentDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    resolver.resolve_location(
        "Elevator in corporate building",
        {"parent_location": "Corporate Building", "description": "The elevator."},
    )
    # The entity created should be for "Corporate Building", not the elevator
    assert "Corporate Building" in db.inserted_names
    assert "Elevator in corporate building" not in db.inserted_names


def test_resolve_location_without_parent_location_creates_exact_name():
    """When no parent_location is in metadata, behaviour is unchanged."""
    import uuid

    class EmptyDB:
        def __init__(self):
            self.inserted_names: list[str] = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            return None

        def fetchval(self, query, params=None, *, commit=False):
            if params and len(params) >= 3:
                self.inserted_names.append(str(params[2]))
            return uuid.uuid4()

        def execute(self, query, params=None):
            pass

    db = EmptyDB()
    resolver = EntityResolver(db, novel_id="novel-1", chapter_number=1)
    resolver.resolve_location("Pemberley", {"description": "Grand estate."})
    assert "Pemberley" in db.inserted_names
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend && .venv/bin/pytest pipeline/extraction/tests/test_resolver.py::test_resolve_location_folds_into_parent_when_parent_location_set pipeline/extraction/tests/test_resolver.py::test_resolve_location_creates_parent_if_missing -v
```

Expected: FAILED (resolver ignores `parent_location` today)

- [ ] **Step 3: Update `resolve_location` in `resolver.py`**

In `EntityResolver`, replace the existing `resolve_location` method:

```python
    def resolve_location(self, name: str, metadata: dict[str, Any] | None = None) -> ResolvedEntity:
        meta = metadata or {}
        parent_name = str(meta.get("parent_location") or "").strip()
        if parent_name:
            # This is a sub-location — always resolve (or create) the parent instead.
            parent_meta = {"description": meta.get("description")}
            return self._resolve("location", parent_name, parent_meta)
        return self._resolve("location", name, meta)
```

- [ ] **Step 4: Run all resolver tests**

```bash
cd backend && .venv/bin/pytest pipeline/extraction/tests/test_resolver.py -v
```

Expected: All pass.

- [ ] **Step 5: Run the full extraction test suite**

```bash
cd backend && .venv/bin/pytest pipeline/extraction/tests/ -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/extraction/resolver.py backend/pipeline/extraction/tests/test_resolver.py
git commit -m "feat: fold sub-locations into parent at resolution time via parent_location metadata"
```

---

## Final Step: Rebuild Frontend + Update Docs

- [ ] **Rebuild frontend**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no TypeScript errors.

- [ ] **Update architecture docs**

In `docs/architecture.html`, find the "Extraction Passes" section and update the `new_entities` row's "What It Extracts" cell to mention the new fields:

> New characters, locations (with `parent_location`), factions, objects (with `owner_name` + `distinguishing_properties`)

In `docs/reference.html`, find the `persist_extras` / `EntityResolver` section and add a note:

> `resolve_location` folds sub-locations into their parent when `parent_location` is set in entity metadata.

- [ ] **Commit docs**

```bash
git add docs/architecture.html docs/reference.html
git commit -m "docs: update extraction pass table and resolver docs for entity dedup changes"
```
