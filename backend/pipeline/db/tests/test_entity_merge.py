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
        "FROM entities WHERE id": [
            {"id": SRC, "entity_type": "character", "name": "Jane", "aliases": ["Janey"]},
        ],
        "FROM characters WHERE entity_id": [
            {"id": SRC_TYPED, "name": "Jane", "aliases": ["Janey"]},
        ],
        "FROM shared_dynamics": [],
        "FROM canon_facts": [],
    })


def test_merge_rejects_type_mismatch():
    db = _db_for_character_merge()
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
            typed = SRC_TYPED if str(params[0]) == SRC else TGT_TYPED
            return [{"id": typed, "name": "x", "aliases": []}]
        return orig(query, params)

    db.script_response = script
    result = merge_entities(db, novel_id=NOVEL, source_entity_id=SRC, target_entity_id=TGT)

    sql = [q for q, _ in db.statements]
    assert any("UPDATE relationships" in q and "entity_a_id" in q for q in sql)
    assert any("DELETE FROM relationships" in q and "entity_b_id = %s" in q for q in sql)
    assert any("UPDATE character_states" in q for q in sql)
    assert any("UPDATE knows_edges" in q for q in sql)
    assert any("involved_characters" in q for q in sql)
    assert any("UPDATE characters" in q and "aliases" in q for q in sql)
    assert any("DELETE FROM characters WHERE id" in q for q in sql)
    assert any("DELETE FROM entities WHERE id" in q for q in sql)
    assert result["source_entity_id"] == SRC


def test_character_merge_repoints_state_deltas_subject_and_object():
    """state_deltas.subject_id/object_id REFERENCE entities(id) ON DELETE CASCADE.
    If the merge deletes the source entity without repointing these first, the
    chapter's possession/knowledge/status deltas vanish silently."""
    db = _db_for_character_merge()
    calls = {"n": 0}
    orig = db.script_response

    def script(query, params):
        if "FROM entities WHERE id" in query:
            calls["n"] += 1
            return [{"id": params[0], "entity_type": "character",
                     "name": "Jane" if calls["n"] == 1 else "Jane Bennet",
                     "aliases": []}]
        if "FROM characters WHERE entity_id" in query:
            typed = SRC_TYPED if str(params[0]) == SRC else TGT_TYPED
            return [{"id": typed, "name": "x", "aliases": []}]
        return orig(query, params)

    db.script_response = script
    merge_entities(db, novel_id=NOVEL, source_entity_id=SRC, target_entity_id=TGT)

    sql_params = [(q, p) for q, p in db.statements]
    subject_updates = [
        (q, p) for q, p in sql_params
        if "UPDATE state_deltas" in q and "subject_id" in q
    ]
    object_updates = [
        (q, p) for q, p in sql_params
        if "UPDATE state_deltas" in q and "object_id" in q
    ]
    assert subject_updates, "expected an UPDATE state_deltas ... subject_id statement"
    assert object_updates, "expected an UPDATE state_deltas ... object_id statement"
    assert subject_updates[0][1] == (TGT, SRC)
    assert object_updates[0][1] == (TGT, SRC)

    sql = [q for q, _ in db.statements]
    subject_idx = next(i for i, q in enumerate(sql) if "UPDATE state_deltas" in q and "subject_id" in q)
    delete_idx = next(i for i, q in enumerate(sql) if "DELETE FROM entities WHERE id" in q)
    assert subject_idx < delete_idx, "state_deltas repoint must happen before the source entity is deleted"


def test_location_merge_repoints_state_deltas_location_id():
    """state_deltas.location_id REFERENCES locations(id) (the typed row id, not
    entities(id)) ON DELETE CASCADE — must repoint with typed ids in the
    location merge path."""
    src_loc_typed = str(uuid.uuid4())
    tgt_loc_typed = str(uuid.uuid4())
    db = MergeFakeDB({
        "FROM shared_dynamics": [],
        "FROM canon_facts": [],
    })
    orig = db.script_response
    calls = {"n": 0}

    def script(query, params):
        if "FROM entities WHERE id" in query:
            calls["n"] += 1
            return [{"id": params[0], "entity_type": "location",
                     "name": "Old Forest" if calls["n"] == 1 else "The Forest",
                     "aliases": []}]
        if "FROM locations WHERE entity_id" in query:
            typed = src_loc_typed if str(params[0]) == SRC else tgt_loc_typed
            return [{"id": typed, "name": "x", "aliases": []}]
        return orig(query, params)

    db.script_response = script
    merge_entities(db, novel_id=NOVEL, source_entity_id=SRC, target_entity_id=TGT)

    loc_updates = [
        (q, p) for q, p in db.statements
        if "UPDATE state_deltas" in q and "location_id" in q
    ]
    assert loc_updates, "expected an UPDATE state_deltas ... location_id statement"
    assert loc_updates[0][1] == (tgt_loc_typed, src_loc_typed)


def test_src_tgt_relationship_deleted_before_repoint():
    """A pre-existing src<->tgt edge must be deleted BEFORE the UPDATEs, or the
    CHECK (entity_a_id <> entity_b_id) constraint aborts the transaction."""
    db = _db_for_character_merge()
    calls = {"n": 0}
    orig = db.script_response

    def script(query, params):
        if "FROM entities WHERE id" in query:
            calls["n"] += 1
            return [{"id": params[0], "entity_type": "character",
                     "name": "Jane" if calls["n"] == 1 else "Jane Bennet",
                     "aliases": []}]
        if "FROM characters WHERE entity_id" in query:
            typed = SRC_TYPED if str(params[0]) == SRC else TGT_TYPED
            return [{"id": typed, "name": "x", "aliases": []}]
        return orig(query, params)

    db.script_response = script
    merge_entities(db, novel_id=NOVEL, source_entity_id=SRC, target_entity_id=TGT)

    sql = [q for q, _ in db.statements]
    pair_delete_idx = next(
        i for i, q in enumerate(sql)
        if "DELETE FROM relationships" in q and "entity_b_id = %s" in q
    )
    first_update_idx = next(i for i, q in enumerate(sql) if "UPDATE relationships" in q)
    assert pair_delete_idx < first_update_idx
    # post-repoint pair dedupe present
    assert any("GREATEST" in q and "DELETE FROM relationships" in q for q in sql)
    # knows_edges.fact_id was dropped from the schema; merge must not reference it
    assert not any("fact_id" in q for q in sql)
