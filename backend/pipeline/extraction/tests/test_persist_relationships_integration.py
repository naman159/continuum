"""Real-Postgres coverage for the relationship insert.

The fake-DB tests record the statement text but never parse it, so a SQL
syntax error reaches production untouched. `symmetric` is a fully reserved
PostgreSQL keyword (it comes from `BETWEEN SYMMETRIC`), which makes the
INSERT column list one such trap: it is legal after a dot (`r.symmetric`) and
as an `AS` label, but not as a bare identifier. Only a real database catches
that, so the insert path gets exercised against one.
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.db.client import DBClient
from pipeline.extraction.resolver import EntityResolver
from pipeline.extraction.persist import persist_extraction


@pytest.fixture(scope="module")
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def novel(db):
    novel_id = str(db.fetchval(
        "INSERT INTO novels (title) VALUES (%s) RETURNING id",
        (f"rel-it-{uuid.uuid4()}",), commit=True,
    ))
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def _character(db, novel_id, name):
    entity_id = str(db.fetchval(
        "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
        (novel_id, "character", name), commit=True,
    ))
    db.execute(
        "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s)",
        (novel_id, entity_id, name),
    )
    return entity_id


def _chapter(db, novel_id, number=2):
    return str(db.fetchval(
        "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s,%s,%s) RETURNING id",
        (novel_id, number, "text"), commit=True,
    ))


def _persist(db, novel_id, chapter_id, rel_overrides=None):
    resolver = EntityResolver(db, novel_id=novel_id, chapter_number=2)
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
    return persist_extraction(
        db, resolver=resolver, chapter_id=chapter_id,
        chapter_number=2, extracted=extracted,
    )


@pytest.mark.parametrize("symmetric", [True, False, None])
def test_relationship_insert_round_trips_symmetric(db, novel, symmetric):
    """`symmetric` is reserved, so an unquoted column list fails to parse."""
    a_id = _character(db, novel, "Alice")
    b_id = _character(db, novel, "Bob")
    chapter_id = _chapter(db, novel)

    overrides = {} if symmetric is None else {"symmetric": symmetric}
    _persist(db, novel, chapter_id, overrides)

    rows = db.fetchall(
        """SELECT entity_a_id, entity_b_id, rel_type, "symmetric", chapter_id
             FROM relationships WHERE chapter_id = %s""",
        (chapter_id,), dict_rows=True,
    )
    assert len(rows) == 1
    row = rows[0]
    assert {str(row["entity_a_id"]), str(row["entity_b_id"])} == {a_id, b_id}
    assert row["rel_type"] == "rival"
    assert row["symmetric"] is symmetric


@pytest.mark.parametrize("mutual,expected", [(False, 2), (True, 1)])
def test_reversed_relationship_keeps_direction_unless_mutual(db, novel, mutual, expected):
    _character(db, novel, "Alice")
    _character(db, novel, "Bob")
    chapter_id = _chapter(db, novel)
    _persist(db, novel, chapter_id, {"rel_type": "mentor_of", "symmetric": mutual})
    _persist(db, novel, chapter_id, {"entity_a": "Bob", "entity_b": "Alice", "rel_type": "mentor_of", "symmetric": mutual})
    assert db.fetchval("SELECT count(*) FROM relationships WHERE chapter_id = %s", (chapter_id,)) == expected


def test_backdated_relationship_does_not_appear_before_its_evidence(db, novel):
    from reads.graphs import relationship_graph
    _character(db, novel, "Alice")
    _character(db, novel, "Bob")
    chapter_id = _chapter(db, novel, number=2)
    _persist(db, novel, chapter_id, {"from_chapter": 1})
    assert relationship_graph(db, novel, 1)["edges"] == []
    assert len(relationship_graph(db, novel, 2)["edges"]) == 1
