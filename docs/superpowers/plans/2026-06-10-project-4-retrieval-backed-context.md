# Project 4: Retrieval-Backed Extraction Context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop injecting the full character/location roster into every extraction prompt; select entities that are actually mentioned in the chapter (plus the most recently active ones) under configurable caps.

**Architecture:** A pure function `select_context_entities` scans the chapter text for roster names/aliases using the same `normalize_name` normalization the canonicalizer uses, returning mentioned entities first and back-filling with the most recently seen ones up to a cap. `load_story_context` gains a `chapter_text` parameter and applies the selection; the roster queries are extended to expose a recency signal (`last_chapter` for characters, `first_appearance_chapter` for locations). Two new settings control the caps.

**Tech Stack:** Python 3.11, pytest with pure-function tests + scripted DB fakes.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/pipeline/extraction/context_select.py` | selection logic (pure) |
| Create | `backend/pipeline/extraction/tests/test_context_select.py` | tests |
| Modify | `backend/pipeline/config.py` | `context_max_characters`, `context_max_locations` |
| Modify | `backend/pipeline/pipeline.py` | `load_story_context(..., chapter_text)` + recency columns |
| Modify | `docs/architecture.html`, `docs/reference.html` | docs |

---

## Task 1: Settings

**Files:**
- Modify: `backend/pipeline/config.py`

- [ ] **Step 1: Add the two settings**

In `backend/pipeline/config.py`, inside `Settings`, after `canonicalizer_max_roster`:

```python
    # Caps for the entity roster injected into extraction prompts. Entities
    # mentioned in the chapter text are always preferred; the remainder is
    # back-filled by recency. <= 0 disables the cap.
    context_max_characters: int = field(
        default_factory=lambda: int(os.getenv("CONTEXT_MAX_CHARACTERS", "40"))
    )
    context_max_locations: int = field(
        default_factory=lambda: int(os.getenv("CONTEXT_MAX_LOCATIONS", "30"))
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/pipeline/config.py
git commit -m "feat(config): context roster caps for extraction prompts"
```

---

## Task 2: `select_context_entities`

**Files:**
- Create: `backend/pipeline/extraction/context_select.py`
- Create: `backend/pipeline/extraction/tests/test_context_select.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/extraction/tests/test_context_select.py`:

```python
from __future__ import annotations

from pipeline.extraction.context_select import select_context_entities


def _chars(*names, **extra):
    return [{"name": n, "aliases": [], **extra} for n in names]


def test_mentioned_entities_are_always_kept():
    roster = _chars("Alice", "Bob", "Carol", "Dave")
    text = "Alice waved at Carol across the square."
    out = select_context_entities(text, roster, cap=2)
    names = [e["name"] for e in out]
    assert names == ["Alice", "Carol"]


def test_alias_mentions_count():
    roster = [
        {"name": "Elizabeth Bennet", "aliases": ["Lizzy"], "last_chapter": 1},
        {"name": "Mr. Collins", "aliases": [], "last_chapter": 2},
    ]
    out = select_context_entities("Lizzy laughed.", roster, cap=1)
    assert out[0]["name"] == "Elizabeth Bennet"


def test_backfill_by_recency():
    roster = [
        {"name": "Old Man", "aliases": [], "last_chapter": 1},
        {"name": "Recent Friend", "aliases": [], "last_chapter": 9},
        {"name": "Mid Person", "aliases": [], "last_chapter": 5},
    ]
    out = select_context_entities("Nobody from the roster appears here.", roster, cap=2)
    names = [e["name"] for e in out]
    assert names == ["Recent Friend", "Mid Person"]


def test_mentions_beat_recency_and_cap_is_respected():
    roster = [
        {"name": "Hot", "aliases": [], "last_chapter": 9},
        {"name": "Cold", "aliases": [], "last_chapter": 1},
        {"name": "Warm", "aliases": [], "last_chapter": 5},
    ]
    out = select_context_entities("Cold stood alone.", roster, cap=2)
    names = [e["name"] for e in out]
    assert names[0] == "Cold"          # mentioned
    assert names[1] == "Hot"           # most recent backfill
    assert len(names) == 2


def test_cap_zero_or_negative_disables():
    roster = _chars("A", "B", "C")
    assert len(select_context_entities("x", roster, cap=0)) == 3


def test_short_names_do_not_false_positive_inside_words():
    roster = [{"name": "Ann", "aliases": [], "last_chapter": 1},
              {"name": "Zed", "aliases": [], "last_chapter": 2}]
    # "Ann" appears only inside "cannon" — not a mention.
    out = select_context_entities("The cannon fired.", roster, cap=1)
    assert out[0]["name"] == "Zed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_context_select.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Implement**

Create `backend/pipeline/extraction/context_select.py`:

```python
from __future__ import annotations

"""Select which roster entities to inject into extraction prompts.

Mentioned-in-text entities always make the cut; the remainder back-fills by
recency (``last_chapter`` / ``first_appearance_chapter``, falling back to 0).
Pure function — DB access stays in load_story_context.
"""

import re
from typing import Any

from pipeline.extraction.canonicalizer import normalize_name


def _mentioned(text_lower: str, entity: dict[str, Any]) -> bool:
    for raw in [entity.get("name", ""), *(entity.get("aliases") or [])]:
        token = normalize_name(str(raw))
        if len(token) < 2:
            continue
        # word-boundary containment; escape regex metacharacters in names
        if re.search(rf"(?<!\w){re.escape(token)}(?!\w)", text_lower):
            return True
    return False


def _recency(entity: dict[str, Any]) -> int:
    for key in ("last_chapter", "first_appearance_chapter"):
        value = entity.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def select_context_entities(
    chapter_text: str,
    roster: list[dict[str, Any]],
    *,
    cap: int,
) -> list[dict[str, Any]]:
    if cap <= 0 or len(roster) <= cap:
        return list(roster)

    text_lower = (chapter_text or "").lower()
    mentioned = [e for e in roster if _mentioned(text_lower, e)]
    if len(mentioned) >= cap:
        return mentioned[:cap]

    mentioned_ids = {id(e) for e in mentioned}
    rest = sorted(
        (e for e in roster if id(e) not in mentioned_ids),
        key=_recency,
        reverse=True,
    )
    return [*mentioned, *rest[: cap - len(mentioned)]]


__all__ = ["select_context_entities"]
```

Note: the `_mentioned` matcher lowercases the chapter text once and uses
word-boundary regex per name; `normalize_name` keeps alias matching consistent
with the canonicalizer (articles, quotes, case).

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_context_select.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extraction/context_select.py backend/pipeline/extraction/tests/test_context_select.py
git commit -m "feat(extraction): mention+recency entity selection for prompt context"
```

---

## Task 3: Wire into `load_story_context`

**Files:**
- Modify: `backend/pipeline/pipeline.py`
- Test: `backend/pipeline/extraction/tests/test_context_select.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/pipeline/extraction/tests/test_context_select.py`:

```python
def test_load_story_context_applies_caps(monkeypatch):
    from pipeline import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod.settings.__class__, "context_max_characters", 1, raising=False)
    monkeypatch.setattr(pipeline_mod.settings.__class__, "context_max_locations", 1, raising=False)

    class CtxFakeDB:
        def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
            if "FROM characters" in query:
                return [
                    {"id": "c1", "name": "Alice", "aliases": [], "emotional_state": None,
                     "goals": None, "physical_state": None, "last_chapter": 1},
                    {"id": "c2", "name": "Bob", "aliases": [], "emotional_state": None,
                     "goals": None, "physical_state": None, "last_chapter": 9},
                ]
            if "FROM locations" in query:
                return [
                    {"id": "l1", "name": "Harbor", "description": None, "first_appearance_chapter": 1},
                    {"id": "l2", "name": "Castle", "description": None, "first_appearance_chapter": 5},
                ]
            return []

    ctx = pipeline_mod.load_story_context(
        CtxFakeDB(), "novel-1", 10, chapter_text="Alice sailed into the Harbor."
    )
    assert [c["name"] for c in ctx["characters"]] == ["Alice"]   # mentioned beats recency
    assert [l["name"] for l in ctx["locations"]] == ["Harbor"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_context_select.py::test_load_story_context_applies_caps -v`
Expected: FAIL — unexpected keyword `chapter_text`

- [ ] **Step 3: Implement**

In `backend/pipeline/pipeline.py`:

1. Import: `from pipeline.extraction.context_select import select_context_entities`

2. Change `load_story_context`'s signature:

```python
def load_story_context(
    db: DBClient,
    novel_id: str,
    chapter_number: int,
    custom_entity_types: list[dict] | None = None,
    chapter_text: str = "",
) -> dict[str, Any]:
```

3. Extend the characters query's LATERAL select so recency is available —
add `ch.number AS last_chapter` to the lateral subquery's SELECT list and
`ls.last_chapter` to the outer SELECT:

```sql
        SELECT c.id, c.name, c.aliases,
               ls.emotional_state, ls.goals, ls.physical_state, ls.last_chapter
        FROM characters c
        LEFT JOIN LATERAL (
            SELECT cs.emotional_state, cs.goals, cs.physical_state, ch.number AS last_chapter
            FROM character_states cs
            JOIN chapters ch ON ch.id = cs.chapter_id
            WHERE cs.character_id = c.id
              AND ch.number < %s
            ORDER BY ch.number DESC
            LIMIT 1
        ) ls ON true
        WHERE c.novel_id = %s
        ORDER BY c.name
```

4. Extend the locations query to include the recency column:

```sql
        SELECT id, name, description, first_appearance_chapter
        FROM locations
        WHERE novel_id = %s
        ORDER BY name
```

5. Apply selection before building the return dict:

```python
    character_rows = [dict(row) for row in characters]
    location_rows = [dict(row) for row in locations]
    if chapter_text:
        character_rows = select_context_entities(
            chapter_text, character_rows, cap=settings.context_max_characters
        )
        location_rows = select_context_entities(
            chapter_text, location_rows, cap=settings.context_max_locations
        )

    return {
        "characters": character_rows,
        "locations": location_rows,
        "open_threads": [dict(row) for row in open_threads],
        "recent_events": [dict(row) for row in recent_events],
        "custom_entities": custom_entities,
    }
```

6. In `process_chapter`, pass the text:

```python
        context = load_story_context(
            db, novel_id, chapter_number,
            custom_entity_types=custom_entity_types,
            chapter_text=raw_text,
        )
```

(If Project 1 has landed, the variable is `client` instead of `db`.)

- [ ] **Step 4: Run the full suite**

Run: `cd backend && .venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/pipeline.py backend/pipeline/extraction/tests/test_context_select.py
git commit -m "feat(pipeline): mention-aware capped story context for extraction prompts"
```

---

## Task 4: Docs

- [ ] **Step 1: Update docs**

- `docs/architecture.html`: in the "Load story context" step, replace "fetches
  existing characters, locations …" with the selection semantics (mentioned
  first, recency backfill, caps).
- `docs/reference.html`: add `CONTEXT_MAX_CHARACTERS` (default 40) and
  `CONTEXT_MAX_LOCATIONS` (default 30) rows to the environment-variable table.
- `docs/state-of-the-system.html`: adjust the cost paragraph (roster no longer
  unbounded in extraction prompts).

- [ ] **Step 2: Commit**

```bash
git add docs/
git commit -m "docs: capped extraction context and new env vars"
```
