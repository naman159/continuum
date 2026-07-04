from __future__ import annotations

import uuid

from pipeline.pipeline import _persist_extraction


class RelFakeDB:
    """Answers the entities lookups EntityResolver makes and the
    duplicate-relationship check; records INSERTs."""

    def __init__(self, *, existing_active_rel: bool):
        self.existing_active_rel = existing_active_rel
        self.inserts: list[tuple[str, tuple]] = []
        self._uid_a = uuid.uuid4()
        self._uid_b = uuid.uuid4()

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


def _persist(db, rel_overrides=None):
    from pipeline.extraction.resolver import EntityResolver

    resolver = EntityResolver(db, novel_id="n1", chapter_number=2)
    rel = {"entity_a": "Alice", "entity_b": "Bob", "rel_type": "rival",
           "from_chapter": 2, "to_chapter": None, "notes": None}
    rel.update(rel_overrides or {})
    extracted = {
        "new_entities": {},
        "entity_deltas": [],
        "events": [],
        "thread_updates": [],
        "continuity_flags": [],
        "dynamics_updates": [],
        "custom_entities": [],
        "canon_facts": [],
        "relationship_updates": [rel],
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


def test_symmetric_bool_from_extractor_is_persisted():
    db = RelFakeDB(existing_active_rel=False)
    _persist(db, {"symmetric": False})
    _, params = db.inserts[0]
    assert params[3] is False


def test_symmetric_missing_persists_as_none():
    db = RelFakeDB(existing_active_rel=False)
    _persist(db)
    _, params = db.inserts[0]
    assert params[3] is None


def test_symmetric_non_bool_persists_as_none():
    db = RelFakeDB(existing_active_rel=False)
    _persist(db, {"symmetric": "true"})
    _, params = db.inserts[0]
    assert params[3] is None


def test_self_referential_relationship_is_skipped():
    # Both names resolve to the same entity (e.g. the extractor emitted the
    # same collective-noun reference for entity_a and entity_b) — inserting
    # would violate the relationships.entity_a_id <> entity_b_id check.
    db = RelFakeDB(existing_active_rel=False)
    _persist(db, {"entity_a": "Alice", "entity_b": "Alice"})
    assert db.inserts == []
