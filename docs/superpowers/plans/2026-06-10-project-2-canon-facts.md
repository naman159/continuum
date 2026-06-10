# Project 2: Canon Facts Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `canon_facts` from a new extraction pass, with upsert-unless-locked semantics, contradiction flags, and a lock/edit API — giving the critic's entity-mention check real data.

**Architecture:** A 13th extraction pass (`canon_facts`) proposes durable facts `{subject_name, subject_type, predicate, value, kind, confidence, quote}`. Facts flow through the existing dedup/canonicalization machinery (subjects are renamed alongside everything else), then `persist_canon_facts` resolves subjects to universal entity ids and upserts: insert when new, update when unlocked and confidence is ≥ existing, and write a `continuity_flags` row (instead of updating) when a **locked** fact is contradicted. Lock/edit/create/delete endpoints land in the existing canon router; the Canon UI gets a lock toggle.

**Tech Stack:** Python 3.11, pytest (mocked LLM), FastAPI, React + TanStack Query.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/pipeline/extraction/prompts.py` | pass schema + instruction |
| Modify | `backend/pipeline/extraction/extractor.py` | normalize/compose/merge `canon_facts` |
| Modify | `backend/pipeline/extraction/canonicalizer.py` | collect + rename fact subjects |
| Create | `backend/pipeline/extraction/persist_canon.py` | persistence with lock semantics |
| Create | `backend/pipeline/extraction/tests/test_canon_facts.py` | pipeline tests |
| Modify | `backend/pipeline/pipeline.py` | call `persist_canon_facts` |
| Modify | `backend/api/queries.py` | mutation queries |
| Modify | `backend/api/routes/canon.py` | PATCH/POST/DELETE endpoints |
| Modify | `backend/api/schemas.py` | request models |
| Create | `backend/api/tests/test_canon_mutations.py` | API tests |
| Modify | `frontend/src/api.ts`, `frontend/src/routes/Canon.tsx`, `frontend/src/routes/Process.tsx` | lock toggle + pass label |
| Modify | `docs/architecture.html`, `docs/reference.html` | docs |

---

## Task 1: Extraction pass — prompts

**Files:**
- Modify: `backend/pipeline/extraction/prompts.py`
- Test: `backend/pipeline/extraction/tests/test_canon_facts.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/extraction/tests/test_canon_facts.py`:

```python
from __future__ import annotations

from pipeline.extraction.prompts import PASS_ORDER, PASS_SCHEMAS, PASS_TASK_INSTRUCTIONS


def test_canon_facts_pass_registered():
    assert "canon_facts" in PASS_ORDER
    assert PASS_ORDER[-1] == "canon_facts"  # appended; jobs total_passes adapts via len()


def test_canon_facts_schema_shape():
    schema = PASS_SCHEMAS["canon_facts"]["canon_facts"][0]
    for key in ("subject_name", "subject_type", "predicate", "value", "kind", "confidence", "quote"):
        assert key in schema


def test_canon_facts_instruction_mentions_durable_and_predicate_style():
    text = PASS_TASK_INSTRUCTIONS["canon_facts"].lower()
    assert "durable" in text or "immutable" in text
    assert "snake_case" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_canon_facts.py -v`
Expected: FAIL — KeyError `'canon_facts'`

- [ ] **Step 3: Implement the prompt pieces**

In `backend/pipeline/extraction/prompts.py`:

Append `"canon_facts"` to `PASS_ORDER` (last element).

Add to `PASS_SCHEMAS`:

```python
    "canon_facts": {
        "canon_facts": [
            {
                "subject_name": "string  # entity the fact is about, exactly as named in the chapter",
                "subject_type": "character|location|object|faction",
                "predicate": "string  # stable snake_case key, e.g. eye_color, home_town, weapon, title, sibling_of",
                "value": "string  # the fact's value, concise",
                "kind": "physical|relational|world_rule|backstory|other",
                "confidence": "number 0.0-1.0",
                "quote": "string  # verbatim supporting sentence from the chapter",
            }
        ]
    },
```

Add to `PASS_TASK_INSTRUCTIONS`:

```python
    "canon_facts": dedent(
        """
        Extract DURABLE, objective facts that future chapters must not
        contradict — physical traits (eye_color, hair_color, height), fixed
        relations (sibling_of, parent_of), origins (home_town, birthplace),
        possessions with identity (signature weapon), and hard world rules
        (magic costs, physical laws of the setting).

        Rules:
        - predicate must be a stable snake_case key; reuse common predicates
          (eye_color, hair_color, title, home_town, weapon, sibling_of,
          parent_of, species, age) rather than inventing synonyms.
        - Only facts explicitly stated or unambiguously shown in this chunk.
        - SKIP transient state (mood, current location, temporary injuries),
          opinions, and speculation. Those belong to other passes.
        - quote must be a verbatim sentence from the chunk supporting the fact.
        - confidence: 1.0 for directly stated, lower for strongly implied.

        Return JSON only.
        """
    ).strip(),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_canon_facts.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extraction/prompts.py backend/pipeline/extraction/tests/test_canon_facts.py
git commit -m "feat(extraction): canon_facts pass schema and instruction"
```

---

## Task 2: Extractor plumbing (normalize, compose, merge)

**Files:**
- Modify: `backend/pipeline/extraction/extractor.py`
- Test: `backend/pipeline/extraction/tests/test_canon_facts.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `backend/pipeline/extraction/tests/test_canon_facts.py`:

```python
from pipeline.extraction.extractor import (
    _normalize_extraction,
    empty_extraction,
    merge_extractions,
)


def test_empty_extraction_has_canon_facts():
    assert empty_extraction()["canon_facts"] == []


def test_normalize_keeps_valid_canon_facts_only():
    raw = {
        "canon_facts": [
            {"subject_name": "Jake", "subject_type": "character", "predicate": "eye_color", "value": "green"},
            {"predicate": "no_subject", "value": "x"},   # missing subject — dropped
            "not a dict",
        ]
    }
    out = _normalize_extraction(raw)
    assert len(out["canon_facts"]) == 1
    assert out["canon_facts"][0]["subject_name"] == "Jake"


def test_merge_extractions_dedupes_canon_by_subject_predicate_keeping_confidence():
    e1 = empty_extraction()
    e1["canon_facts"] = [
        {"subject_name": "Jake", "subject_type": "character", "predicate": "eye_color",
         "value": "green", "confidence": 0.6},
    ]
    e2 = empty_extraction()
    e2["canon_facts"] = [
        {"subject_name": "jake", "subject_type": "character", "predicate": "eye_color",
         "value": "emerald green", "confidence": 0.9},
        {"subject_name": "Jake", "subject_type": "character", "predicate": "home_town",
         "value": "Busan", "confidence": 1.0},
    ]
    merged = merge_extractions([e1, e2])
    facts = {(f["subject_name"].lower(), f["predicate"]): f for f in merged["canon_facts"]}
    assert len(facts) == 2
    assert facts[("jake", "eye_color")]["value"] == "emerald green"  # higher confidence wins
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_canon_facts.py -v -k "empty or normalize_keeps or merge_extractions_dedupes_canon"`
Expected: FAIL — KeyError `'canon_facts'`

- [ ] **Step 3: Implement**

In `backend/pipeline/extraction/extractor.py`:

1. In `empty_extraction()`, add `"canon_facts": [],` after `"custom_entities": []`.

2. In `_normalize_extraction`, after the `custom_entities` block, add:

```python
    canon_facts = raw.get("canon_facts", [])
    if isinstance(canon_facts, list):
        output["canon_facts"] = [
            item for item in canon_facts
            if isinstance(item, dict)
            and str(item.get("subject_name", "")).strip()
            and str(item.get("predicate", "")).strip()
            and str(item.get("value", "")).strip()
        ]
```

3. In `merge_extractions`, after the custom-entities block, add:

```python
    # Canon facts: dedupe by (subject, predicate); highest confidence wins.
    best_canon: dict[tuple[str, str], dict[str, Any]] = {}
    for extraction in extractions:
        for fact in extraction.get("canon_facts", []):
            if not isinstance(fact, dict):
                continue
            subj = str(fact.get("subject_name", "")).strip().lower()
            pred = str(fact.get("predicate", "")).strip().lower()
            if not subj or not pred:
                continue
            key = (subj, pred)
            current = best_canon.get(key)
            new_conf = float(fact.get("confidence") or 0.0)
            if current is None or new_conf > float(current.get("confidence") or 0.0):
                best_canon[key] = fact
    merged["canon_facts"] = list(best_canon.values())
```

4. In `ChapterExtractor._compose_from_pass_payload`, add a local
`canon = pass_payload.get("canon_facts", {})` and include in the returned dict:

```python
            "canon_facts": canon.get("canon_facts", []),
```

- [ ] **Step 4: Run the extraction suite**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extraction/extractor.py backend/pipeline/extraction/tests/test_canon_facts.py
git commit -m "feat(extraction): canon_facts normalize/compose/merge"
```

---

## Task 3: Dedup integration (subjects rename with everything else)

**Files:**
- Modify: `backend/pipeline/extraction/canonicalizer.py`
- Test: `backend/pipeline/extraction/tests/test_canon_facts.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/pipeline/extraction/tests/test_canon_facts.py`:

```python
from pipeline.extraction.canonicalizer import collect_names_by_type, _apply_rename_map


def test_canon_subjects_collected_and_renamed():
    extracted = {
        "canon_facts": [
            {"subject_name": "Jane", "subject_type": "character", "predicate": "eye_color", "value": "blue"},
            {"subject_name": "Netherfield", "subject_type": "location", "predicate": "region", "value": "north"},
        ],
    }
    by_type = collect_names_by_type(extracted)
    assert "Jane" in by_type["character"]
    assert "Netherfield" in by_type["location"]

    renamed = _apply_rename_map(
        extracted,
        {"character": {"jane": "Jane Bennet"}, "location": {"netherfield": "Netherfield Park"}},
    )
    assert renamed["canon_facts"][0]["subject_name"] == "Jane Bennet"
    assert renamed["canon_facts"][1]["subject_name"] == "Netherfield Park"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_canon_facts.py::test_canon_subjects_collected_and_renamed -v`
Expected: FAIL

- [ ] **Step 3: Implement**

In `backend/pipeline/extraction/canonicalizer.py`:

In `collect_names_by_type`, before the `custom_entities` loop, add:

```python
    for fact in extracted.get("canon_facts", []) or []:
        if not isinstance(fact, dict):
            continue
        subject_type = str(fact.get("subject_type", "")).strip().lower()
        if subject_type in result:
            _add_name(str(fact.get("subject_name", "")), subject_type, result)
```

In `_apply_rename_map`, before the custom-entities block, add:

```python
    for fact in data.get("canon_facts", []) or []:
        if not isinstance(fact, dict):
            continue
        subject_type = str(fact.get("subject_type", "")).strip().lower()
        fact["subject_name"] = _r(str(fact.get("subject_name", "")), rename_map.get(subject_type, {}))
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extraction/canonicalizer.py backend/pipeline/extraction/tests/test_canon_facts.py
git commit -m "feat(dedup): canon fact subjects flow through collect/rename"
```

---

## Task 4: Persistence with lock semantics

**Files:**
- Create: `backend/pipeline/extraction/persist_canon.py`
- Modify: `backend/pipeline/pipeline.py`
- Test: `backend/pipeline/extraction/tests/test_canon_facts.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `backend/pipeline/extraction/tests/test_canon_facts.py`:

```python
import uuid

from pipeline.extraction.persist_canon import persist_canon_facts


class FakeResolver:
    def __init__(self):
        self.uid = str(uuid.uuid4())

    def resolve_character(self, name, metadata=None):
        from pipeline.extraction.resolver import ResolvedEntity
        return ResolvedEntity(entity_id="typed-id", universal_id=self.uid, created=False)

    resolve_location = resolve_object = resolve_faction = resolve_character


class CanonFakeDB:
    """Scripts fetchone for the existing-fact lookup; records writes."""

    def __init__(self, existing=None):
        self.existing = existing  # dict row or None
        self.calls: list[tuple[str, tuple]] = []

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        self.calls.append((query, tuple(params or ())))
        if "FROM canon_facts" in query:
            return self.existing
        return None

    def execute(self, query, params=None):
        self.calls.append((query, tuple(params or ())))


FACT = {
    "subject_name": "Jake", "subject_type": "character",
    "predicate": "eye_color", "value": "green",
    "kind": "physical", "confidence": 0.9, "quote": "His green eyes narrowed.",
}


def test_persist_inserts_new_fact():
    db = CanonFakeDB(existing=None)
    counts = persist_canon_facts(
        db, novel_id="n1", chapter_id="ch1", chapter_number=3,
        facts=[FACT], resolver=FakeResolver(),
    )
    assert counts == {"inserted": 1, "updated": 0, "contradictions": 0, "skipped": 0}
    assert any("INSERT INTO canon_facts" in q for q, _ in db.calls)


def test_persist_updates_unlocked_when_confidence_not_lower():
    db = CanonFakeDB(existing={"id": "f1", "value": "blue", "locked": False, "confidence": 0.5})
    counts = persist_canon_facts(
        db, novel_id="n1", chapter_id="ch1", chapter_number=3,
        facts=[FACT], resolver=FakeResolver(),
    )
    assert counts["updated"] == 1
    assert any("UPDATE canon_facts" in q for q, _ in db.calls)


def test_persist_flags_contradiction_of_locked_fact():
    db = CanonFakeDB(existing={"id": "f1", "value": "blue", "locked": True, "confidence": 1.0})
    counts = persist_canon_facts(
        db, novel_id="n1", chapter_id="ch1", chapter_number=3,
        facts=[FACT], resolver=FakeResolver(),
    )
    assert counts["contradictions"] == 1
    assert any("INSERT INTO continuity_flags" in q for q, _ in db.calls)
    assert not any("UPDATE canon_facts" in q for q, _ in db.calls)


def test_persist_skips_unknown_subject_type():
    db = CanonFakeDB()
    bad = dict(FACT, subject_type="spaceship")
    counts = persist_canon_facts(
        db, novel_id="n1", chapter_id="ch1", chapter_number=3,
        facts=[bad], resolver=FakeResolver(),
    )
    assert counts["skipped"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_canon_facts.py -v -k persist`
Expected: FAIL — ModuleNotFoundError `persist_canon`

- [ ] **Step 3: Implement `persist_canon.py`**

Create `backend/pipeline/extraction/persist_canon.py`:

```python
from __future__ import annotations

"""Persistence for the canon_facts extraction pass.

Semantics:
- new (novel, subject, predicate)        -> INSERT
- existing, unlocked, conf >= existing   -> UPDATE value/confidence/source
- existing, unlocked, conf <  existing   -> skip (keep stronger fact)
- existing, LOCKED, same value           -> no-op
- existing, LOCKED, different value      -> continuity_flag (canon_contradiction)
"""

import logging
from typing import Any

from pipeline.db.client import DBClient
from pipeline.extraction.resolver import EntityResolver

logger = logging.getLogger(__name__)

_SUBJECT_RESOLVERS = {
    "character": "resolve_character",
    "location": "resolve_location",
    "object": "resolve_object",
    "faction": "resolve_faction",
}


def persist_canon_facts(
    db: DBClient,
    *,
    novel_id: str,
    chapter_id: str,
    chapter_number: int,
    facts: list[dict[str, Any]],
    resolver: EntityResolver,
) -> dict[str, int]:
    counts = {"inserted": 0, "updated": 0, "contradictions": 0, "skipped": 0}
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        subject_name = str(fact.get("subject_name", "")).strip()
        subject_type = str(fact.get("subject_type", "")).strip().lower()
        predicate = str(fact.get("predicate", "")).strip().lower()
        value = str(fact.get("value", "")).strip()
        if not subject_name or not predicate or not value:
            counts["skipped"] += 1
            continue
        method = _SUBJECT_RESOLVERS.get(subject_type)
        if method is None:
            logger.warning("canon fact subject_type %r not supported — skipped", subject_type)
            counts["skipped"] += 1
            continue
        subject_universal_id = getattr(resolver, method)(subject_name).universal_id

        try:
            confidence = max(0.0, min(1.0, float(fact.get("confidence") or 1.0)))
        except (TypeError, ValueError):
            confidence = 1.0
        kind = str(fact.get("kind") or "other").strip().lower()

        existing = db.fetchone(
            """
            SELECT id, value, locked, confidence
              FROM canon_facts
             WHERE novel_id = %s AND subject_entity_id = %s AND predicate = %s
            """,
            (novel_id, subject_universal_id, predicate),
            dict_rows=True,
        )

        if existing is None:
            db.execute(
                """
                INSERT INTO canon_facts (
                    novel_id, kind, subject_entity_id, predicate, value,
                    source_chapter, confidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (novel_id, kind, subject_universal_id, predicate, value,
                 chapter_number, confidence),
            )
            counts["inserted"] += 1
            continue

        same_value = value.lower() == str(existing["value"]).strip().lower()
        if existing["locked"]:
            if not same_value:
                db.execute(
                    """
                    INSERT INTO continuity_flags (chapter_id, description, flag_type)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        chapter_id,
                        (
                            f"Chapter contradicts locked canon: {subject_name}."
                            f"{predicate} is locked to {existing['value']!r} but this "
                            f"chapter says {value!r}. Quote: {fact.get('quote') or 'n/a'}"
                        ),
                        "canon_contradiction",
                    ),
                )
                counts["contradictions"] += 1
            continue

        if same_value or confidence >= float(existing.get("confidence") or 0.0):
            db.execute(
                """
                UPDATE canon_facts
                   SET value = %s, confidence = %s, source_chapter = %s, kind = %s
                 WHERE id = %s
                """,
                (value, confidence, chapter_number, kind, existing["id"]),
            )
            counts["updated"] += 1
        else:
            counts["skipped"] += 1
    return counts


__all__ = ["persist_canon_facts"]
```

- [ ] **Step 4: Wire into `process_chapter`**

In `backend/pipeline/pipeline.py`:

Add import: `from pipeline.extraction.persist_canon import persist_canon_facts`

Inside the persistence block (after `persist_commitments(...)`; if Project 1 has
landed, this sits inside `with client.session() as s:` and receives `s`,
otherwise it receives `db`):

```python
        persist_canon_facts(
            db,  # or `s` when Project 1's session block is in place
            novel_id=novel_id,
            chapter_id=chapter_id,
            chapter_number=chapter_number,
            facts=extracted.get("canon_facts", []),
            resolver=resolver,
        )
```

- [ ] **Step 5: Run the full suite**

Run: `cd backend && .venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/extraction/persist_canon.py backend/pipeline/pipeline.py backend/pipeline/extraction/tests/test_canon_facts.py
git commit -m "feat(pipeline): persist canon facts with lock/contradiction semantics"
```

---

## Task 5: API mutations (lock / edit / create / delete)

**Files:**
- Modify: `backend/api/schemas.py`
- Modify: `backend/api/queries.py`
- Modify: `backend/api/routes/canon.py`
- Create: `backend/api/tests/test_canon_mutations.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/api/tests/test_canon_mutations.py` (these test the queries layer
directly with a SQL recorder, mirroring `test_queries_real_sql.py`, because the
canon tables have no in-memory FakeDB representation):

```python
from __future__ import annotations

from uuid import uuid4

from api import queries


class _Recorder:
    def __init__(self, fetchone_result=None):
        self.calls: list[tuple[str, tuple]] = []
        self._fetchone_result = fetchone_result

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        self.calls.append((query, tuple(params or ())))
        return self._fetchone_result

    def execute(self, query, params=None):
        self.calls.append((query, tuple(params or ())))

    def fetchval(self, query, params=None, *, commit=False):
        self.calls.append((query, tuple(params or ())))
        return 1


def test_patch_canon_fact_sets_lock(monkeypatch):
    db = _Recorder(fetchone_result={"id": uuid4()})
    monkeypatch.setattr(queries, "_get_db", lambda: db)
    ok = queries.update_canon_fact(uuid4(), uuid4(), locked=True, value=None)
    assert ok is True
    assert any("UPDATE canon_facts" in q and "locked" in q for q, _ in db.calls)


def test_patch_canon_fact_missing_returns_false(monkeypatch):
    db = _Recorder(fetchone_result=None)
    monkeypatch.setattr(queries, "_get_db", lambda: db)
    assert queries.update_canon_fact(uuid4(), uuid4(), locked=True, value=None) is False


def test_create_canon_fact_inserts(monkeypatch):
    db = _Recorder(fetchone_result={"id": uuid4()})
    monkeypatch.setattr(queries, "_get_db", lambda: db)
    row = queries.create_canon_fact(
        uuid4(),
        subject_entity_id=uuid4(),
        predicate="eye_color",
        value="green",
        kind="physical",
        locked=True,
    )
    assert row is not None
    assert any("INSERT INTO canon_facts" in q for q, _ in db.calls)


def test_delete_canon_fact(monkeypatch):
    db = _Recorder(fetchone_result={"id": uuid4()})
    monkeypatch.setattr(queries, "_get_db", lambda: db)
    assert queries.delete_canon_fact(uuid4(), uuid4()) is True
    assert any("DELETE FROM canon_facts" in q for q, _ in db.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest api/tests/test_canon_mutations.py -v`
Expected: FAIL — AttributeError `update_canon_fact`

- [ ] **Step 3: Implement queries**

Append to `backend/api/queries.py` (after `list_canon_facts`):

```python
def update_canon_fact(
    novel_id: UUID, fact_id: UUID, *, locked: bool | None, value: str | None
) -> bool:
    db = _get_db()
    existing = db.fetchone(
        "SELECT id FROM canon_facts WHERE id = %s AND novel_id = %s",
        (str(fact_id), str(novel_id)),
        dict_rows=True,
    )
    if existing is None:
        return False
    if locked is not None:
        db.execute(
            "UPDATE canon_facts SET locked = %s WHERE id = %s AND novel_id = %s",
            (locked, str(fact_id), str(novel_id)),
        )
    if value is not None:
        db.execute(
            "UPDATE canon_facts SET value = %s, confidence = 1.0 WHERE id = %s AND novel_id = %s",
            (value, str(fact_id), str(novel_id)),
        )
    return True


def create_canon_fact(
    novel_id: UUID,
    *,
    subject_entity_id: UUID,
    predicate: str,
    value: str,
    kind: str = "other",
    locked: bool = False,
) -> dict[str, Any] | None:
    db = _get_db()
    row = db.fetchone(
        """
        INSERT INTO canon_facts (novel_id, kind, subject_entity_id, predicate, value, confidence, locked)
        VALUES (%s, %s, %s, %s, %s, 1.0, %s)
        ON CONFLICT (novel_id, subject_entity_id, predicate)
        DO UPDATE SET value = EXCLUDED.value, locked = EXCLUDED.locked, confidence = 1.0
        RETURNING id, kind, subject_entity_id, predicate, value, source_chapter, confidence, locked
        """,
        (str(novel_id), kind, str(subject_entity_id), predicate.strip().lower(), value, locked),
        dict_rows=True,
        commit=True,
    )
    return dict(row) if row else None


def delete_canon_fact(novel_id: UUID, fact_id: UUID) -> bool:
    db = _get_db()
    existing = db.fetchone(
        "SELECT id FROM canon_facts WHERE id = %s AND novel_id = %s",
        (str(fact_id), str(novel_id)),
        dict_rows=True,
    )
    if existing is None:
        return False
    db.execute(
        "DELETE FROM canon_facts WHERE id = %s AND novel_id = %s",
        (str(fact_id), str(novel_id)),
    )
    return True
```

- [ ] **Step 4: Add schemas and routes**

In `backend/api/schemas.py` add:

```python
class CanonFactPatch(BaseModel):
    locked: bool | None = None
    value: str | None = None


class CanonFactCreate(BaseModel):
    subject_entity_id: UUID
    predicate: str
    value: str
    kind: str = "other"
    locked: bool = False
```

In `backend/api/routes/canon.py` add (imports: `HTTPException`, the two new
schemas, `status`):

```python
@router.patch("/api/novels/{novel_id}/canon/{fact_id}")
def patch_canon_fact(novel_id: UUID, fact_id: UUID, body: CanonFactPatch) -> dict:
    if body.locked is None and body.value is None:
        raise HTTPException(status_code=422, detail="nothing to update")
    ok = queries.update_canon_fact(novel_id, fact_id, locked=body.locked, value=body.value)
    if not ok:
        raise HTTPException(status_code=404, detail="Canon fact not found")
    return {"ok": True}


@router.post("/api/novels/{novel_id}/canon", status_code=status.HTTP_201_CREATED)
def post_canon_fact(novel_id: UUID, body: CanonFactCreate) -> dict:
    row = queries.create_canon_fact(
        novel_id,
        subject_entity_id=body.subject_entity_id,
        predicate=body.predicate,
        value=body.value,
        kind=body.kind,
        locked=body.locked,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="insert failed")
    return {"id": str(row["id"])}


@router.delete("/api/novels/{novel_id}/canon/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_canon_fact(novel_id: UUID, fact_id: UUID) -> None:
    if not queries.delete_canon_fact(novel_id, fact_id):
        raise HTTPException(status_code=404, detail="Canon fact not found")
```

(Match the existing router's path style — if its `APIRouter` already has a
`prefix`, express the paths relative to it.)

- [ ] **Step 5: Run tests**

Run: `cd backend && .venv/bin/pytest api/tests -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api/queries.py backend/api/routes/canon.py backend/api/schemas.py backend/api/tests/test_canon_mutations.py
git commit -m "feat(api): canon fact lock/edit/create/delete endpoints"
```

---

## Task 6: Frontend (lock toggle + pass label) and docs

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/routes/Canon.tsx`
- Modify: `frontend/src/routes/Process.tsx`
- Modify: `docs/architecture.html`, `docs/reference.html`

- [ ] **Step 1: Add the API call**

In `frontend/src/api.ts`, add to the `api` object:

```typescript
  patchCanonFact: async (novelId: string, factId: string, patch: { locked?: boolean; value?: string }) => {
    const res = await fetch(`/api/novels/${novelId}/canon/${factId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json() as Promise<{ ok: boolean }>;
  },
```

- [ ] **Step 2: Add the lock toggle in `Canon.tsx`**

In `frontend/src/routes/Canon.tsx`, add a mutation and render a toggle button
in each fact row (adapt to the file's existing row markup):

```tsx
const queryClient = useQueryClient();
const lockMutation = useMutation({
  mutationFn: ({ factId, locked }: { factId: string; locked: boolean }) =>
    api.patchCanonFact(novelId!, factId, { locked }),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ["canon", novelId] }),
});
```

```tsx
<button
  type="button"
  title={fact.locked ? "Unlock (allow extraction to update)" : "Lock (contradictions become flags)"}
  onClick={() => lockMutation.mutate({ factId: fact.id, locked: !fact.locked })}
>
  {fact.locked ? "🔒 Locked" : "🔓 Lock"}
</button>
```

(Use the query key that `Canon.tsx` already uses for its `useQuery` —
invalidate that exact key.)

- [ ] **Step 3: Add the pass label**

In `frontend/src/routes/Process.tsx`, add to `PASS_LABELS`:

```typescript
  canon_facts: "Extracting canon facts",
```

- [ ] **Step 4: Build the frontend**

Run: `cd frontend && npm run build`
Expected: build succeeds, no TS errors.

- [ ] **Step 5: Update docs**

- `docs/architecture.html`: extraction-passes table gains the `canon_facts`
  row (now 13 passes — update any "12 passes" copy); chapter-processing list
  notes canon persistence semantics (upsert-unless-locked, contradiction →
  continuity flag).
- `docs/reference.html`: canon endpoints (PATCH/POST/DELETE) documented; the
  `canon_facts` pass schema added.
- `docs/state-of-the-system.html`: remove/adjust the "No canon-fact pass" gap
  bullet.

- [ ] **Step 6: Commit**

```bash
git add frontend/src docs/
git commit -m "feat(ui+docs): canon fact lock toggle, canon_facts pass label, docs"
```
