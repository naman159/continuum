"""Real-Postgres coverage for cross-type merges.

The fake-DB tests assert which statements get issued; they cannot catch a
foreign-key violation, an array function applied to NULL, or a typo in the
`involved_*` column move. Those are exactly the ways the cross-type path can
fail, so it gets exercised against a real database.
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.db.client import DBClient
from pipeline.db.entity_merge import merge_entities


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
        (f"merge-it-{uuid.uuid4()}",), commit=True,
    ))
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def _entity(db, novel_id, entity_type, name):
    return str(db.fetchval(
        "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
        (novel_id, entity_type, name), commit=True,
    ))


def _typed(db, table, novel_id, entity_id, name):
    return str(db.fetchval(
        f"INSERT INTO {table} (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
        (novel_id, entity_id, name), commit=True,
    ))


def _chapter(db, novel_id, number=1):
    return str(db.fetchval(
        "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s,%s,%s) RETURNING id",
        (novel_id, number, "text"), commit=True,
    ))


def test_cross_type_merge_moves_event_involvement(db, novel):
    """The real failure this repairs: one thing extracted as a character in one
    chapter and an object in the next. The event still involves it afterwards,
    just as an object."""
    src_e = _entity(db, novel, "character", "Healer class")
    tgt_e = _entity(db, novel, "object", "Healer")
    src_t = _typed(db, "characters", novel, src_e, "Healer class")
    tgt_t = _typed(db, "objects", novel, tgt_e, "Healer")
    chapter_id = _chapter(db, novel)

    event_id = str(db.fetchval(
        """INSERT INTO events (chapter_id, description, involved_characters)
           VALUES (%s,%s,%s) RETURNING id""",
        (chapter_id, "the healer class is chosen", [src_t]), commit=True,
    ))

    result = merge_entities(db, novel_id=novel, source_entity_id=src_e, target_entity_id=tgt_e)
    assert result["cross_type"] is True

    row = db.fetchall(
        "SELECT involved_characters, involved_objects FROM events WHERE id = %s",
        (event_id,), dict_rows=True,
    )[0]
    assert [str(x) for x in (row["involved_characters"] or [])] == []
    assert [str(x) for x in (row["involved_objects"] or [])] == [tgt_t]

    assert db.fetchval("SELECT count(*) FROM characters WHERE id = %s", (src_t,)) == 0
    assert db.fetchval("SELECT count(*) FROM entities WHERE id = %s", (src_e,)) == 0
    assert db.fetchval("SELECT count(*) FROM objects WHERE id = %s", (tgt_t,)) == 1


def test_cross_type_merge_survives_non_cascading_scene_refs(db, novel):
    """scenes.pov_character_id has no ON DELETE CASCADE — without the explicit
    detach this merge raises a foreign-key violation."""
    src_e = _entity(db, novel, "character", "Tutorial")
    tgt_e = _entity(db, novel, "location", "Tutorial Room")
    src_t = _typed(db, "characters", novel, src_e, "Tutorial")
    _typed(db, "locations", novel, tgt_e, "Tutorial Room")
    chapter_id = _chapter(db, novel)

    scene_id = str(db.fetchval(
        """INSERT INTO scenes (chapter_id, scene_index, pov_character_id, present_characters)
           VALUES (%s,%s,%s,%s) RETURNING id""",
        (chapter_id, 0, src_t, [src_t]), commit=True,
    ))

    merge_entities(db, novel_id=novel, source_entity_id=src_e, target_entity_id=tgt_e)

    row = db.fetchall(
        "SELECT pov_character_id, present_characters FROM scenes WHERE id = %s",
        (scene_id,), dict_rows=True,
    )[0]
    assert row["pov_character_id"] is None
    assert [str(x) for x in (row["present_characters"] or [])] == []


def test_cross_type_merge_keeps_entity_level_edges(db, novel):
    """Relationships key off entities.id, so they are type-agnostic and must
    survive the reclassification rather than dying with the typed row."""
    src_e = _entity(db, novel, "character", "System")
    tgt_e = _entity(db, novel, "faction", "System")
    _typed(db, "characters", novel, src_e, "System")
    _typed(db, "factions", novel, tgt_e, "System")
    other_e = _entity(db, novel, "character", "Jake")
    _typed(db, "characters", novel, other_e, "Jake")

    db.execute(
        "INSERT INTO relationships (entity_a_id, entity_b_id, rel_type) VALUES (%s,%s,%s)",
        (src_e, other_e, "guided by"),
    )

    merge_entities(db, novel_id=novel, source_entity_id=src_e, target_entity_id=tgt_e)

    surviving = db.fetchall(
        "SELECT entity_a_id, entity_b_id FROM relationships WHERE entity_a_id = %s OR entity_b_id = %s",
        (tgt_e, tgt_e), dict_rows=True,
    )
    assert len(surviving) == 1
    pair = {str(surviving[0]["entity_a_id"]), str(surviving[0]["entity_b_id"])}
    assert pair == {tgt_e, other_e}


def test_cross_type_merge_absorbs_name_as_alias(db, novel):
    src_e = _entity(db, novel, "character", "Archer Class")
    tgt_e = _entity(db, novel, "object", "Archer class")
    _typed(db, "characters", novel, src_e, "Archer Class")
    tgt_t = _typed(db, "objects", novel, tgt_e, "Archer class")

    merge_entities(db, novel_id=novel, source_entity_id=src_e, target_entity_id=tgt_e)

    typed_aliases = db.fetchval("SELECT aliases FROM objects WHERE id = %s", (tgt_t,))
    entity_aliases = db.fetchval("SELECT aliases FROM entities WHERE id = %s", (tgt_e,))
    # Case-insensitive self-alias is dropped; here the names differ only by case
    # so the absorbed name is correctly *not* duplicated onto the target.
    assert list(typed_aliases or []) == []
    assert list(entity_aliases or []) == []


def test_same_type_merge_still_works(db, novel):
    """Regression guard: the cross-type branch must not have disturbed the
    original same-type path."""
    src_e = _entity(db, novel, "character", "Jane")
    tgt_e = _entity(db, novel, "character", "Jane Bennet")
    src_t = _typed(db, "characters", novel, src_e, "Jane")
    tgt_t = _typed(db, "characters", novel, tgt_e, "Jane Bennet")
    chapter_id = _chapter(db, novel)
    event_id = str(db.fetchval(
        """INSERT INTO events (chapter_id, description, involved_characters)
           VALUES (%s,%s,%s) RETURNING id""",
        (chapter_id, "Jane speaks", [src_t]), commit=True,
    ))

    result = merge_entities(db, novel_id=novel, source_entity_id=src_e, target_entity_id=tgt_e)
    assert result["cross_type"] is False

    involved = db.fetchval("SELECT involved_characters FROM events WHERE id = %s", (event_id,))
    assert [str(x) for x in (involved or [])] == [tgt_t]
    assert "Jane" in list(db.fetchval("SELECT aliases FROM characters WHERE id = %s", (tgt_t,)) or [])
