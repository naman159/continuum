from __future__ import annotations

import uuid

from pipeline.pipeline import _persist_extraction


class DynFakeDB:
    """Answers the entities lookups EntityResolver makes; records INSERTs."""

    def __init__(self):
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
        return None

    def fetchval(self, query, params=None, *, commit=False):
        return uuid.uuid4()

    def execute(self, query, params=None):
        if "INSERT INTO shared_dynamics" in query:
            self.inserts.append((query, tuple(params or ())))


def _persist(db, dyn_overrides=None):
    from pipeline.extraction.resolver import EntityResolver

    resolver = EntityResolver(db, novel_id="n1", chapter_number=2)
    dyn = {"entity_a": "Alice", "entity_b": "Bob", "description": "Tense standoff"}
    dyn.update(dyn_overrides or {})
    extracted = {
        "new_entities": {},
        "entity_deltas": [],
        "events": [],
        "thread_updates": [],
        "continuity_flags": [],
        "relationship_updates": [],
        "custom_entities": [],
        "canon_facts": [],
        "dynamics_updates": [dyn],
    }
    return _persist_extraction(
        db, resolver=resolver, chapter_id=str(uuid.uuid4()),
        chapter_number=2, extracted=extracted,
    )


def test_new_dynamic_is_inserted():
    db = DynFakeDB()
    _persist(db)
    assert len(db.inserts) == 1


def test_self_referential_dynamic_is_skipped():
    # Both names resolve to the same entity (e.g. a collective-noun group
    # description like "the group") — inserting would violate
    # shared_dynamics.entity_a_id <> entity_b_id.
    db = DynFakeDB()
    _persist(db, {"entity_a": "Alice", "entity_b": "Alice"})
    assert db.inserts == []
