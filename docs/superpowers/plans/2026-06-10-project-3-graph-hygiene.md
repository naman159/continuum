# Project 3: Graph Hygiene — Relationship Dedupe + Entity Merge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop duplicate relationship rows accumulating across chapters, and provide an entity merge operation (API + CLI) so wrong LLM dedup decisions can be repaired before they compound.

**Architecture:** (1) `_persist_extraction` checks for an existing active relationship with the same unordered entity pair + rel_type before inserting. (2) A new `pipeline/db/entity_merge.py` implements `merge_entities` in one `db.transaction()`: it repoints every reference from the source entity to the target (universal-id references at the entities level, typed-id references per entity type), unions aliases (source name becomes a target alias so the resolver keeps resolving it), and deletes the source rows. Conflict-prone repoints (unique constraints, self-loops) are computed row-by-row in Python inside the transaction rather than with clever SQL.

**Tech Stack:** Python 3.11, psycopg3, pytest with scripted fakes, FastAPI.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/pipeline/pipeline.py` | relationship dedupe-before-insert |
| Create | `backend/pipeline/extraction/tests/test_persist_relationships.py` | dedupe tests |
| Create | `backend/pipeline/db/entity_merge.py` | merge operation |
| Create | `backend/pipeline/db/tests/test_entity_merge.py` | merge tests |
| Create | `backend/cli/merge.py` | `novel-wiki-merge-entity` CLI |
| Modify | `backend/pyproject.toml` | script entry |
| Modify | `backend/api/routes/entity_types.py` | merge endpoint |
| Modify | `backend/api/schemas.py` | request model |
| Modify | `docs/architecture.html`, `docs/reference.html` | docs |

---

## Task 1: Relationship dedupe at persist time

**Files:**
- Modify: `backend/pipeline/pipeline.py` (`_persist_extraction`, relationships loop)
- Create: `backend/pipeline/extraction/tests/test_persist_relationships.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/extraction/tests/test_persist_relationships.py`:

```python
from __future__ import annotations

import uuid

from pipeline.pipeline import _persist_extraction


class RelFakeDB:
    """Answers the entities/typed lookups EntityResolver makes and the
    duplicate-relationship check; records INSERTs."""

    def __init__(self, *, existing_active_rel: bool):
        self.existing_active_rel = existing_active_rel
        self.inserts: list[tuple[str, tuple]] = []
        self._uid_a = uuid.uuid4()
        self._uid_b = uuid.uuid4()

    # resolve_any_entity path: entities-by-name lookup succeeds
    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        if "FROM entities" in query and params:
            name = str(params[1]).lower()
            if name == "alice":
                return (self._uid_a,)
            if name == "bob":
                return (self._uid_b,)
            return None
        if "FROM relationships" in query and "superseded_by_id IS NULL" in query:
            return ("existing-rel-id",) if self.existing_active_rel else None
        return None

    def fetchval(self, query, params=None, *, commit=False):
        return uuid.uuid4()

    def execute(self, query, params=None):
        if "INSERT INTO relationships" in query:
            self.inserts.append((query, tuple(params or ())))


class NullResolver:
    """Real EntityResolver against RelFakeDB."""


def _persist(db):
    from pipeline.extraction.resolver import EntityResolver

    resolver = EntityResolver(db, novel_id="n1", chapter_number=2)
    extracted = {
        "new_entities": {},
        "entity_deltas": [],
        "events": [],
        "thread_updates": [],
        "continuity_flags": [],
        "dynamics_updates": [],
        "custom_entities": [],
        "relationship_updates": [
            {"entity_a": "Alice", "entity_b": "Bob", "rel_type": "rival",
             "from_chapter": 2, "to_chapter": None, "notes": None}
        ],
    }
    return _persist_extraction(
        db, resolver=resolver, chapter_id=str(uuid.uuid4()),
        chapter_number=2, extracted=extracted,
    )


def test_duplicate_active_relationship_is_not_reinserted():
    db = RelFakeDB(existing_active_rel=True)
    _persist(db)
    assert db.inserts == []


def test_new_relationship_is_inserted():
    db = RelFakeDB(existing_active_rel=False)
    _persist(db)
    assert len(db.inserts) == 1
```

- [ ] **Step 2: Run tests to verify the dedupe one fails**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_persist_relationships.py -v`
Expected: `test_duplicate_active_relationship_is_not_reinserted` FAILS (insert happened)

- [ ] **Step 3: Implement the dedupe check**

In `backend/pipeline/pipeline.py`, in `_persist_extraction`'s
`relationship_updates` loop, after resolving `a_universal`/`b_universal` and
before the INSERT, add:

```python
        # Identical active relationship already recorded -> don't re-insert.
        # Different rel_types between the same pair coexist by design.
        duplicate = db.fetchone(
            """
            SELECT id FROM relationships
             WHERE rel_type IS NOT DISTINCT FROM %s
               AND superseded_by_id IS NULL
               AND (
                     (entity_a_id = %s AND entity_b_id = %s)
                  OR (entity_a_id = %s AND entity_b_id = %s)
               )
             LIMIT 1
            """,
            (rel.get("rel_type"), a_universal, b_universal, b_universal, a_universal),
        )
        if duplicate:
            continue
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/pytest pipeline/extraction/tests/test_persist_relationships.py -v && .venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/pipeline.py backend/pipeline/extraction/tests/test_persist_relationships.py
git commit -m "feat(pipeline): skip duplicate active relationships at persist time"
```

---

## Task 2: `merge_entities` core

**Files:**
- Create: `backend/pipeline/db/entity_merge.py`
- Create: `backend/pipeline/db/tests/test_entity_merge.py` (create `backend/pipeline/db/tests/__init__.py` if Project 1 hasn't)

### Design

`merge_entities(db, *, novel_id, source_entity_id, target_entity_id) -> dict`
where both ids are **universal** (`entities.id`). Inside one
`db.transaction()` cursor:

1. Validate: both rows exist in this novel, same `entity_type`, not identical.
2. Entity-level repoints (apply to every type): `relationships`
   (a/b columns, then delete self-loops), `shared_dynamics` (row-by-row in
   Python: skip→delete on collision with the UNIQUE(a,b,chapter) constraint or
   self-loop), `canon_facts.subject_entity_id` (pre-delete source facts whose
   predicate already exists on target), `commitments.related_entity_ids`
   (array replace + dedupe), `located_in_edges.entity_id`, events SVO columns.
3. Typed repoints when a dedicated table exists (look up typed ids via
   `entity_id`): per type, rewrite the `events.involved_*` array, plus
   character: `character_states.character_id`, `scenes.pov_character_id`,
   `scenes.present_characters`, `knows_edges.character_id`,
   `knows_edges.shared_with`, `possesses_edges.character_id`;
   location: `character_states.location_id`, `scenes.location_id`,
   `locations.parent_location_id`, `located_in_edges.location_id`;
   object: `possesses_edges.object_id`.
4. Alias union: target typed row (or target entities row for custom types)
   gains source's aliases + source's name.
5. Delete source typed row, then source entities row.

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/db/tests/test_entity_merge.py`:

```python
from __future__ import annotations

import uuid

import pytest

from pipeline.db.entity_merge import EntityMergeError, merge_entities


class TxCursor:
    def __init__(self, db):
        self.db = db

    def execute(self, query, params=None):
        self.db.statements.append((query, tuple(params or ())))
        self.db._last = self.db.script_response(query, params)

    def fetchone(self):
        return self.db._last[0] if self.db._last else None

    def fetchall(self):
        return list(self.db._last or [])


class MergeFakeDB:
    """Scripted transaction-cursor fake. `responses` maps a query substring to
    a list of rows; first match wins; unmatched queries return []."""

    def __init__(self, responses):
        self.responses = responses
        self.statements: list[tuple[str, tuple]] = []
        self._last: list = []

    def script_response(self, query, params):
        for needle, rows in self.responses.items():
            if needle in query:
                return rows
        return []

    # context manager mimicking DBClient.transaction()
    def transaction(self):
        db = self

        class _Ctx:
            def __enter__(self_inner):
                return TxCursor(db)

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()


NOVEL = str(uuid.uuid4())
SRC = str(uuid.uuid4())
TGT = str(uuid.uuid4())
SRC_TYPED = str(uuid.uuid4())
TGT_TYPED = str(uuid.uuid4())


def _db_for_character_merge():
    return MergeFakeDB({
        # entity validation lookup (both rows, keyed by id param)
        "FROM entities WHERE id": [
            {"id": SRC, "entity_type": "character", "name": "Jane", "aliases": ["Janey"]},
        ],
        # typed-row lookups
        "FROM characters WHERE entity_id": [
            {"id": SRC_TYPED, "name": "Jane", "aliases": ["Janey"]},
        ],
        # shared_dynamics touching source
        "FROM shared_dynamics": [],
        # canon predicate collision check
        "FROM canon_facts": [],
    })


def test_merge_rejects_type_mismatch():
    db = MergeFakeDB({
        "FROM entities WHERE id": [
            {"id": SRC, "entity_type": "character", "name": "Jane", "aliases": []},
        ],
    })

    # Patch the per-id lookup to return differing types
    calls = {"n": 0}
    orig = db.script_response

    def script(query, params):
        if "FROM entities WHERE id" in query:
            calls["n"] += 1
            etype = "character" if calls["n"] == 1 else "location"
            return [{"id": params[0], "entity_type": etype, "name": "X", "aliases": []}]
        return orig(query, params)

    db.script_response = script
    with pytest.raises(EntityMergeError):
        merge_entities(db, novel_id=NOVEL, source_entity_id=SRC, target_entity_id=TGT)


def test_merge_rejects_same_ids():
    db = _db_for_character_merge()
    with pytest.raises(EntityMergeError):
        merge_entities(db, novel_id=NOVEL, source_entity_id=SRC, target_entity_id=SRC)


def test_character_merge_repoints_and_deletes_source():
    db = _db_for_character_merge()

    calls = {"n": 0}
    orig = db.script_response

    def script(query, params):
        if "FROM entities WHERE id" in query:
            calls["n"] += 1
            return [{"id": params[0], "entity_type": "character",
                     "name": "Jane" if calls["n"] == 1 else "Jane Bennet",
                     "aliases": ["Janey"] if calls["n"] == 1 else []}]
        if "FROM characters WHERE entity_id" in query:
            # first call: source typed row; second: target typed row
            typed = SRC_TYPED if str(params[0]) == SRC else TGT_TYPED
            return [{"id": typed, "name": "x", "aliases": []}]
        return orig(query, params)

    db.script_response = script
    result = merge_entities(db, novel_id=NOVEL, source_entity_id=SRC, target_entity_id=TGT)

    sql = [q for q, _ in db.statements]
    assert any("UPDATE relationships" in q and "entity_a_id" in q for q in sql)
    assert any("DELETE FROM relationships" in q and "entity_a_id = entity_b_id" in q for q in sql)
    assert any("UPDATE character_states" in q for q in sql)
    assert any("UPDATE knows_edges" in q for q in sql)
    assert any("involved_characters" in q for q in sql)
    assert any("UPDATE characters" in q and "aliases" in q for q in sql)  # alias union
    assert any("DELETE FROM characters WHERE id" in q for q in sql)
    assert any("DELETE FROM entities WHERE id" in q for q in sql)
    assert result["source_entity_id"] == SRC
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest pipeline/db/tests/test_entity_merge.py -v`
Expected: FAIL — ModuleNotFoundError `entity_merge`

- [ ] **Step 3: Implement `entity_merge.py`**

Create `backend/pipeline/db/entity_merge.py`:

```python
from __future__ import annotations

"""Merge one entity into another, repairing wrong dedup decisions.

All statements run in a single transaction. Source and target must belong to
the same novel and share an entity_type. After the merge the source's name and
aliases become aliases of the target, so the resolver keeps resolving old
references to the surviving entity.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class EntityMergeError(ValueError):
    pass


_TYPED_TABLE = {
    "character": "characters",
    "location": "locations",
    "object": "objects",
    "faction": "factions",
}

_INVOLVED_COLUMN = {
    "character": "involved_characters",
    "location": "involved_locations",
    "object": "involved_objects",
    "faction": "involved_factions",
}


def _fetch_entity(cur, entity_id: str, novel_id: str) -> dict[str, Any]:
    cur.execute(
        "SELECT id, entity_type, name, aliases FROM entities WHERE id = %s AND novel_id = %s",
        (entity_id, novel_id),
    )
    row = cur.fetchone()
    if row is None:
        raise EntityMergeError(f"entity {entity_id} not found in novel {novel_id}")
    if isinstance(row, dict):
        return row
    return {"id": row[0], "entity_type": row[1], "name": row[2], "aliases": list(row[3] or [])}


def _fetch_typed(cur, table: str, entity_id: str) -> dict[str, Any] | None:
    cur.execute(
        f"SELECT id, name, aliases FROM {table} WHERE entity_id = %s LIMIT 1",
        (entity_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return {"id": row[0], "name": row[1], "aliases": list(row[2] or [])}


def _replace_in_array_column(cur, table: str, column: str, src: str, tgt: str, *, extra_where: str = "", extra_params: tuple = ()) -> None:
    cur.execute(
        f"""
        UPDATE {table}
           SET {column} = ARRAY(
                 SELECT DISTINCT x
                   FROM unnest(array_replace({column}, %s::uuid, %s::uuid)) AS x
               )
         WHERE %s::uuid = ANY({column}) {extra_where}
        """,
        (src, tgt, src, *extra_params),
    )


def merge_entities(
    db: Any, *, novel_id: str, source_entity_id: str, target_entity_id: str
) -> dict[str, Any]:
    if str(source_entity_id) == str(target_entity_id):
        raise EntityMergeError("source and target are the same entity")

    with db.transaction() as cur:
        source = _fetch_entity(cur, str(source_entity_id), str(novel_id))
        target = _fetch_entity(cur, str(target_entity_id), str(novel_id))
        if source["entity_type"] != target["entity_type"]:
            raise EntityMergeError(
                f"type mismatch: {source['entity_type']} vs {target['entity_type']}"
            )
        entity_type = str(source["entity_type"])
        src, tgt = str(source["id"]), str(target["id"])

        # ---- entity-level references (apply to every type) ----
        cur.execute("UPDATE relationships SET entity_a_id = %s WHERE entity_a_id = %s", (tgt, src))
        cur.execute("UPDATE relationships SET entity_b_id = %s WHERE entity_b_id = %s", (tgt, src))
        cur.execute("DELETE FROM relationships WHERE entity_a_id = entity_b_id", None)

        # shared_dynamics: UNIQUE(a, b, chapter) — handle collisions row by row.
        cur.execute(
            "SELECT id, entity_a_id, entity_b_id, chapter_id FROM shared_dynamics "
            "WHERE entity_a_id = %s OR entity_b_id = %s",
            (src, src),
        )
        for row in cur.fetchall():
            r = row if isinstance(row, dict) else {
                "id": row[0], "entity_a_id": row[1], "entity_b_id": row[2], "chapter_id": row[3]
            }
            new_a = tgt if str(r["entity_a_id"]) == src else str(r["entity_a_id"])
            new_b = tgt if str(r["entity_b_id"]) == src else str(r["entity_b_id"])
            if new_a == new_b:
                cur.execute("DELETE FROM shared_dynamics WHERE id = %s", (r["id"],))
                continue
            cur.execute(
                "SELECT id FROM shared_dynamics WHERE entity_a_id = %s AND entity_b_id = %s "
                "AND chapter_id = %s AND id <> %s",
                (new_a, new_b, r["chapter_id"], r["id"]),
            )
            if cur.fetchone() is not None:
                cur.execute("DELETE FROM shared_dynamics WHERE id = %s", (r["id"],))
            else:
                cur.execute(
                    "UPDATE shared_dynamics SET entity_a_id = %s, entity_b_id = %s WHERE id = %s",
                    (new_a, new_b, r["id"]),
                )

        # canon_facts: drop source facts whose predicate the target already has.
        cur.execute(
            """
            DELETE FROM canon_facts s
             WHERE s.subject_entity_id = %s
               AND EXISTS (
                 SELECT 1 FROM canon_facts t
                  WHERE t.subject_entity_id = %s AND t.predicate = s.predicate
               )
            """,
            (src, tgt),
        )
        cur.execute(
            "UPDATE canon_facts SET subject_entity_id = %s WHERE subject_entity_id = %s",
            (tgt, src),
        )

        _replace_in_array_column(cur, "commitments", "related_entity_ids", src, tgt,
                                 extra_where="AND novel_id = %s", extra_params=(str(novel_id),))
        cur.execute("UPDATE located_in_edges SET entity_id = %s WHERE entity_id = %s", (tgt, src))
        cur.execute("UPDATE events SET subject_entity_id = %s WHERE subject_entity_id = %s", (tgt, src))
        cur.execute("UPDATE events SET object_entity_id = %s WHERE object_entity_id = %s", (tgt, src))

        # ---- typed-table references ----
        table = _TYPED_TABLE.get(entity_type)
        if table is not None:
            src_typed = _fetch_typed(cur, table, src)
            tgt_typed = _fetch_typed(cur, table, tgt)
            if src_typed is None or tgt_typed is None:
                raise EntityMergeError(f"typed rows missing for {entity_type} merge")
            st, tt = str(src_typed["id"]), str(tgt_typed["id"])

            _replace_in_array_column(cur, "events", _INVOLVED_COLUMN[entity_type], st, tt)

            if entity_type == "character":
                cur.execute("UPDATE character_states SET character_id = %s WHERE character_id = %s", (tt, st))
                cur.execute("UPDATE scenes SET pov_character_id = %s WHERE pov_character_id = %s", (tt, st))
                _replace_in_array_column(cur, "scenes", "present_characters", st, tt)
                cur.execute("UPDATE knows_edges SET character_id = %s WHERE character_id = %s", (tt, st))
                _replace_in_array_column(cur, "knows_edges", "shared_with", st, tt)
                cur.execute("UPDATE possesses_edges SET character_id = %s WHERE character_id = %s", (tt, st))
            elif entity_type == "location":
                cur.execute("UPDATE character_states SET location_id = %s WHERE location_id = %s", (tt, st))
                cur.execute("UPDATE scenes SET location_id = %s WHERE location_id = %s", (tt, st))
                cur.execute("UPDATE locations SET parent_location_id = %s WHERE parent_location_id = %s", (tt, st))
                cur.execute("UPDATE located_in_edges SET location_id = %s WHERE location_id = %s", (tt, st))
            elif entity_type == "object":
                cur.execute("UPDATE possesses_edges SET object_id = %s WHERE object_id = %s", (tt, st))

            # alias union: target absorbs source's name + aliases.
            merged_aliases = list(dict.fromkeys(
                [*(tgt_typed.get("aliases") or []),
                 *(src_typed.get("aliases") or []),
                 str(src_typed.get("name") or source["name"])]
            ))
            merged_aliases = [
                a for a in merged_aliases
                if a and a.lower() != str(tgt_typed.get("name", "")).lower()
            ]
            cur.execute(
                f"UPDATE {table} SET aliases = %s WHERE id = %s",
                (merged_aliases, tt),
            )
            cur.execute(f"DELETE FROM {table} WHERE id = %s", (st,))
        else:
            # custom type: aliases live on the entities row.
            merged_aliases = list(dict.fromkeys(
                [*(target.get("aliases") or []), *(source.get("aliases") or []), str(source["name"])]
            ))
            merged_aliases = [
                a for a in merged_aliases if a and a.lower() != str(target["name"]).lower()
            ]
            cur.execute(
                "UPDATE entities SET aliases = %s WHERE id = %s",
                (merged_aliases, tgt),
            )

        cur.execute("DELETE FROM entities WHERE id = %s", (src,))

    logger.info("merged entity %s into %s (%s)", src, tgt, entity_type)
    return {
        "source_entity_id": src,
        "target_entity_id": tgt,
        "entity_type": entity_type,
        "absorbed_name": str(source["name"]),
    }


__all__ = ["merge_entities", "EntityMergeError"]
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/pytest pipeline/db/tests/test_entity_merge.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/db/entity_merge.py backend/pipeline/db/tests/test_entity_merge.py
git commit -m "feat(db): merge_entities operation for repairing wrong dedup"
```

---

## Task 3: API endpoint + CLI

**Files:**
- Modify: `backend/api/schemas.py`
- Modify: `backend/api/routes/entity_types.py`
- Create: `backend/cli/merge.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/api/tests/test_entity_merge_api.py` (create)

- [ ] **Step 1: Write the failing API test**

Create `backend/api/tests/test_entity_merge_api.py`:

```python
from __future__ import annotations

from uuid import uuid4


def test_merge_endpoint_validates_and_calls_merge(client, monkeypatch):
    captured: dict = {}

    def fake_merge(db, *, novel_id, source_entity_id, target_entity_id):
        captured.update(novel_id=novel_id, src=source_entity_id, tgt=target_entity_id)
        return {"source_entity_id": source_entity_id, "target_entity_id": target_entity_id,
                "entity_type": "character", "absorbed_name": "Jane"}

    monkeypatch.setattr("api.routes.entity_types.merge_entities", fake_merge)
    monkeypatch.setattr("api.routes.entity_types._merge_db", lambda: object())

    novel_id, src, tgt = uuid4(), uuid4(), uuid4()
    response = client.post(
        f"/api/novels/{novel_id}/entities/merge",
        json={"source_entity_id": str(src), "target_entity_id": str(tgt)},
    )
    assert response.status_code == 200
    assert captured["src"] == str(src)


def test_merge_endpoint_maps_merge_error_to_400(client, monkeypatch):
    from pipeline.db.entity_merge import EntityMergeError

    def fake_merge(db, **kwargs):
        raise EntityMergeError("type mismatch")

    monkeypatch.setattr("api.routes.entity_types.merge_entities", fake_merge)
    monkeypatch.setattr("api.routes.entity_types._merge_db", lambda: object())

    response = client.post(
        f"/api/novels/{uuid4()}/entities/merge",
        json={"source_entity_id": str(uuid4()), "target_entity_id": str(uuid4())},
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest api/tests/test_entity_merge_api.py -v`
Expected: FAIL — 404 (route missing)

- [ ] **Step 3: Implement the endpoint**

In `backend/api/schemas.py` add:

```python
class EntityMergeRequest(BaseModel):
    source_entity_id: UUID
    target_entity_id: UUID
```

In `backend/api/routes/entity_types.py` add imports and the route:

```python
from fastapi import HTTPException

from api.schemas import EntityMergeRequest
from pipeline.db.client import DBClient
from pipeline.db.entity_merge import EntityMergeError, merge_entities


def _merge_db() -> DBClient:
    """Separate factory so tests can stub the merge connection."""
    return DBClient()


@router.post("/api/novels/{novel_id}/entities/merge")
def merge_novel_entities(novel_id: UUID, body: EntityMergeRequest) -> dict:
    db = _merge_db()
    try:
        return merge_entities(
            db,
            novel_id=str(novel_id),
            source_entity_id=str(body.source_entity_id),
            target_entity_id=str(body.target_entity_id),
        )
    except EntityMergeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if hasattr(db, "close"):
            db.close()
```

(If this router uses a `prefix`, express the path relative to it.)

- [ ] **Step 4: Implement the CLI**

Create `backend/cli/merge.py`:

```python
"""CLI: `novel-wiki-merge-entity` — merge a duplicate entity into its canonical twin."""

from __future__ import annotations

import argparse
import json

from pipeline.db.client import DBClient
from pipeline.db.entity_merge import EntityMergeError, merge_entities


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge entity SOURCE into TARGET")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--source", required=True, help="entities.id to be absorbed (disappears)")
    parser.add_argument("--target", required=True, help="entities.id that survives")
    args = parser.parse_args()

    with DBClient() as db:
        try:
            result = merge_entities(
                db,
                novel_id=args.novel_id,
                source_entity_id=args.source,
                target_entity_id=args.target,
            )
        except EntityMergeError as exc:
            raise SystemExit(f"merge refused: {exc}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
```

In `backend/pyproject.toml`, add under `[project.scripts]`:

```toml
novel-wiki-merge-entity = "cli.merge:main"
```

- [ ] **Step 5: Run all tests**

Run: `cd backend && .venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api backend/cli/merge.py backend/pyproject.toml
git commit -m "feat(api+cli): entity merge endpoint and novel-wiki-merge-entity command"
```

---

## Task 4: Manual verification + docs

- [ ] **Step 1: Manual verification against the dev DB**

```bash
cd backend
# find two duplicate entities (or create a throwaway pair) and merge:
.venv/bin/python -c "
from pipeline.db.client import DBClient
with DBClient() as db:
    rows = db.fetchall(\"SELECT id, entity_type, name FROM entities ORDER BY name LIMIT 10\", dict_rows=True)
    [print(r) for r in rows]
"
# .venv/bin/python -m cli.merge --novel-id <N> --source <SRC> --target <TGT>
```

Expected: merge prints a result JSON; the source disappears from the entity
graph; its name appears among the target's aliases.

- [ ] **Step 2: Update docs**

- `docs/architecture.html`: add a short "Repair: entity merge" paragraph to the
  entity-resolution section; note relationship dedupe semantics (identical
  active pair+type rows are not re-inserted).
- `docs/reference.html`: document `POST /api/novels/{id}/entities/merge` and
  the `novel-wiki-merge-entity` CLI.
- `docs/state-of-the-system.html`: adjust the dedup/out-of-scope notes (manual
  merge now exists).

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: entity merge and relationship dedupe"
```
