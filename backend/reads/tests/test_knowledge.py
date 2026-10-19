from __future__ import annotations

from reads import knowledge as knowledge_reads


def test_canon_facts_cutoff(db, seed_novel):
    seeded = seed_novel(db)  # factory canon fact: source_chapter=2
    assert knowledge_reads.list_canon_facts(db, seeded["novel_id"], up_to_chapter=1, locked_only=False) == []
    assert knowledge_reads.list_canon_facts(db, seeded["novel_id"], up_to_chapter=2, locked_only=False)


def test_edges_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    for fn in (knowledge_reads.list_location_edges, knowledge_reads.list_possession_edges):
        rows = fn(db, seeded["novel_id"], up_to_chapter=1, active_only=False)
        assert all(r["since_chapter"] <= 1 for r in rows)


def test_focused_ownership_filters(db, seed_novel):
    from uuid import uuid4

    seeded = seed_novel(db)
    rows = knowledge_reads.list_possession_edges(
        db, seeded["novel_id"], 2, True,
        character_id=seeded["char_a_id"], object_id=seeded["obj_id"],
    )
    assert len(rows) == 1
    assert str(rows[0]["object_id"]) == seeded["obj_id"]
    assert knowledge_reads.list_possession_edges(
        db, seeded["novel_id"], 2, True, object_id=uuid4()
    ) == []
    assert knowledge_reads.list_possession_edges(
        db, seeded["novel_id"], 0, True, object_id=seeded["obj_id"]
    ) == []


def test_focused_location_filters(db, seed_novel):
    seeded = seed_novel(db)
    rows = knowledge_reads.list_location_edges(
        db, seeded["novel_id"], 2, True,
        location_id=seeded["loc_a_id"], entity_name=seeded["char_a_name"].lower(),
    )
    assert rows
    assert all(str(r["entity_id"]) == seeded["char_a_eid"] for r in rows)
    assert knowledge_reads.list_location_edges(
        db, seeded["novel_id"], 2, True, entity_name="not a recorded entity"
    ) == []


def test_knowledge_filters_and_later_replacement_preserve_past(db, seed_novel):
    seeded = seed_novel(db)
    old = db.fetchval(
        """INSERT INTO knows_edges (character_id, fact_description, learned_chapter)
           VALUES (%s, 'The king is dead', 1) RETURNING id""", (seeded["char_a_id"],), commit=True
    )
    new = db.fetchval(
        """INSERT INTO knows_edges (character_id, fact_description, learned_chapter)
           VALUES (%s, 'The king survived', 3) RETURNING id""", (seeded["char_a_id"],), commit=True
    )
    db.execute("UPDATE knows_edges SET superseded_by_id = %s WHERE id = %s", (new, old))
    rows = knowledge_reads.list_knows_edges(db, seeded["novel_id"], 2, None, "KING IS DEAD")
    assert [r["id"] for r in rows] == [old]
    assert knowledge_reads.list_knows_edges(db, seeded["novel_id"], 3, None, "king is dead") == []
    assert knowledge_reads.list_knows_edges(db, seeded["novel_id"], 2, seeded["char_b_id"], "king") == []


def test_later_possession_replacement_preserves_earlier_owner(db, seed_novel):
    seeded = seed_novel(db)
    old = db.fetchval(
        "SELECT id FROM possesses_edges WHERE character_id = %s AND object_id = %s",
        (seeded["char_a_id"], seeded["obj_id"]),
    )
    new = db.fetchval(
        """INSERT INTO possesses_edges (character_id, object_id, since_chapter)
           VALUES (%s, %s, 3) RETURNING id""",
        (seeded["char_b_id"], seeded["obj_id"]), commit=True,
    )
    db.execute("UPDATE possesses_edges SET superseded_by_id = %s, until_chapter = 3 WHERE id = %s", (new, old))
    past = knowledge_reads.list_possession_edges(db, seeded["novel_id"], 2, True, object_id=seeded["obj_id"])
    current = knowledge_reads.list_possession_edges(db, seeded["novel_id"], 3, True, object_id=seeded["obj_id"])
    assert [str(r["character_id"]) for r in past] == [seeded["char_a_id"]]
    assert past[0]["until_chapter"] is None
    assert [str(r["character_id"]) for r in current] == [seeded["char_b_id"]]
