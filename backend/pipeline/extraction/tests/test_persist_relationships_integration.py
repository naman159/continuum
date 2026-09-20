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


def _persist(db, novel_id, chapter_id, rel_overrides=None, *, number=2):
    resolver = EntityResolver(db, novel_id=novel_id, chapter_number=number)
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
        chapter_number=number, extracted=extracted,
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


@pytest.mark.parametrize("overrides,expected", [
    ({}, 1),
    ({"symmetric": "true"}, 1),
    ({"entity_b": "Alice"}, 0),
    ({"entity_b": "Ghost"}, 0),
])
def test_repeated_or_invalid_relationships_do_not_add_edges(db, novel, overrides, expected):
    _character(db, novel, "Alice")
    _character(db, novel, "Bob")
    chapter_id = _chapter(db, novel)
    _persist(db, novel, chapter_id, overrides)
    _persist(db, novel, chapter_id, overrides)
    rows = db.fetchall('SELECT "symmetric" FROM relationships WHERE chapter_id = %s', (chapter_id,))
    assert len(rows) == expected
    if rows:
        assert rows[0][0] is None


@pytest.mark.parametrize("other_type", ["character", "object", "custom"])
def test_relationship_ending_preserves_history_and_replacement(db, novel, other_type):
    from reads.characters import get_character_detail
    from reads.graphs import entity_graph, relationship_graph
    from reads.world import get_custom_entity_detail, get_object_detail


    a_id = _character(db, novel, "Alice")
    if other_type == "character":
        b_id = _character(db, novel, "Bob")
    else:
        b_id = str(db.fetchval(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,'Bob') RETURNING id",
            (novel, "object" if other_type == "object" else "magic"), commit=True,
        ))
        if other_type == "object":
            object_id = db.fetchval(
                "INSERT INTO objects (novel_id, entity_id, name) VALUES (%s,%s,'Bob') RETURNING id",
                (novel, b_id), commit=True,
            )
    alice = db.fetchval("SELECT id FROM characters WHERE entity_id = %s", (a_id,))
    ch2, ch3 = _chapter(db, novel, 2), _chapter(db, novel, 3)
    _persist(db, novel, ch2, {"notes": "original evidence"})
    _persist(db, novel, ch3, {"from_chapter": 3, "to_chapter": 3, "notes": "ending evidence"}, number=3)

    def visible(cap):
        graph = entity_graph(db, novel, cap)
        count = len([e for e in graph["edges"] if e["edge_kind"] == "relationship"])
        detail = get_character_detail(db, novel, alice, cap)
        assert len(detail["relationships"]) == count
        if other_type == "character":
            assert len(relationship_graph(db, novel, cap)["edges"]) == count
        elif other_type == "object":
            assert len(get_object_detail(db, novel, object_id, cap)["relationships"]) == count
        else:
            assert len(get_custom_entity_detail(db, novel, b_id, cap)["relationships"]) == count
        return count

    assert visible(1) == 0
    assert visible(2) == 1
    assert get_character_detail(db, novel, alice, 2)["relationships"][0]["notes"] == "original evidence"
    assert visible(3) == 1  # to_chapter is the final active chapter (inclusive).
    assert visible(4) == 0
    assert db.fetchval("SELECT count(*) FROM relationships WHERE entity_a_id = %s", (a_id,)) == 2

    # Chapter replacement deletes the ending assertion; its predecessor must
    # become active again without violating the self-referencing foreign key.
    db.execute("DELETE FROM chapters WHERE id = %s", (ch3,))
    assert visible(4) == 1
    ch3 = _chapter(db, novel, 3)
    _persist(db, novel, ch3, {"to_chapter": 3}, number=3)
    assert visible(4) == 0
    ch5 = _chapter(db, novel, 5)
    _persist(db, novel, ch5, {"from_chapter": 5}, number=5)
    assert visible(4) == 0
    assert visible(5) == 1


def test_relationship_ending_rolls_back_with_chapter_transaction(db, novel):
    _character(db, novel, "Alice")
    _character(db, novel, "Bob")
    ch2, ch3 = _chapter(db, novel, 2), _chapter(db, novel, 3)
    _persist(db, novel, ch2)
    with pytest.raises(RuntimeError, match="later persistence failure"):
        with db.session() as session:
            _persist(session, novel, ch3, {"to_chapter": 3}, number=3)
            raise RuntimeError("later persistence failure")
    assert db.fetchval("SELECT count(*) FROM relationships WHERE chapter_id = %s", (ch3,)) == 0
    assert db.fetchval("SELECT superseded_by_id FROM relationships WHERE chapter_id = %s", (ch2,)) is None


def test_entity_merge_keeps_relationship_history_and_direction(db, novel):
    from pipeline.db.entity_merge import merge_entities
    from reads.graphs import relationship_graph

    from pipeline.db.history import capture_metadata

    a_id = _character(db, novel, "Alice")
    alias_id = _character(db, novel, "Alias")
    _character(db, novel, "Bob")
    capture_metadata(db, novel, 0)
    ch2, ch3, ch4 = (_chapter(db, novel, n) for n in (2, 3, 4))
    _persist(db, novel, ch2)
    _persist(db, novel, ch3, {"to_chapter": 3}, number=3)
    _persist(db, novel, ch4, {"entity_a": "Alias", "rel_type": "mentor", "symmetric": False}, number=4)
    _persist(db, novel, ch4, {"entity_a": "Bob", "entity_b": "Alice", "rel_type": "mentor", "symmetric": False}, number=4)
    merge_entities(db, novel_id=novel, source_entity_id=alias_id, target_entity_id=a_id)
    assert db.fetchval("SELECT count(*) FROM relationships WHERE entity_a_id = %s OR entity_b_id = %s", (a_id, a_id)) == 4
    assert len(relationship_graph(db, novel, 2)["edges"]) == 1
    assert [e["label"] for e in relationship_graph(db, novel, 4)["edges"]] == ["mentor", "mentor"]
