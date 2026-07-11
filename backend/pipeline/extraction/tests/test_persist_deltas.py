"""persist_state_deltas resolves names reference-only and writes typed rows.

Hits the real branch-isolated Postgres (same idiom as test_materializer).
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.db.client import DBClient
from pipeline.extraction.persist_deltas import persist_state_deltas
from pipeline.extraction.resolver import EntityResolver


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seeded(db: DBClient):
    novel_id = str(uuid.uuid4())
    db.execute("INSERT INTO novels (id, title) VALUES (%s, %s)", (novel_id, f"T-{novel_id[:8]}"))
    chapter_id = db.fetchval(
        "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s, 1, 'x') RETURNING id",
        (novel_id,), commit=True,
    )
    resolver = EntityResolver(db, novel_id=novel_id, chapter_number=1)
    resolver.resolve_character("Aelric", {"description": "a knight"})
    resolver.resolve_object("silver dagger", {"description": "a blade"})
    resolver.resolve_location("Pellis Harbor", {"description": "a port"})
    yield {"novel_id": novel_id, "chapter_id": str(chapter_id), "resolver": resolver}
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_persists_each_kind_and_drops_unknown_names(db: DBClient, seeded):
    deltas = [
        {"kind": "possession", "character_name": "Aelric", "object_name": "silver dagger",
         "change": "gain", "quote": "he took it"},
        {"kind": "location", "character_name": "Aelric", "location_name": "Pellis Harbor",
         "change": "move", "quote": "he arrived"},
        {"kind": "knowledge", "character_name": "Aelric", "fact": "the harbor is watched",
         "change": "learn", "quote": "he realized"},
        {"kind": "status", "character_name": "Aelric", "attribute": "emotional_state",
         "value": "wary", "change": "update", "quote": "wary now"},
        # Unknown character: dropped, never minted (reference-only).
        {"kind": "status", "character_name": "Sword Skill Lv.3", "attribute": "notes",
         "value": "x", "change": "update", "quote": "q"},
        # Possession of an unknown object: dropped.
        {"kind": "possession", "character_name": "Aelric", "object_name": "unknown orb",
         "change": "gain", "quote": "q"},
    ]
    written = persist_state_deltas(
        db, chapter_id=seeded["chapter_id"], deltas=deltas, resolver=seeded["resolver"]
    )
    assert written == 4
    rows = db.fetchall(
        "SELECT kind, change, attribute, detail, ordinal FROM state_deltas WHERE chapter_id = %s ORDER BY ordinal",
        (seeded["chapter_id"],), dict_rows=True,
    )
    assert [r["kind"] for r in rows] == ["possession", "location", "knowledge", "status"]
    assert [r["ordinal"] for r in rows] == [0, 1, 2, 3]
    assert rows[2]["detail"] == "the harbor is watched"
    assert rows[3]["attribute"] == "emotional_state" and rows[3]["detail"] == "wary"
    # No phantom character was created for the game-system name.
    assert db.fetchval(
        "SELECT count(*) FROM characters WHERE novel_id = %s", (seeded["novel_id"],)
    ) == 1
