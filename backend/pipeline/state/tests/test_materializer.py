"""End-to-end tests for the state_deltas-replaying state materializer.

These hit the real branch-isolated Postgres DB. They seed a tiny novel
with typed state_deltas rows, run materialize(), and assert that:

  - character_states rows exist for each (character, chapter) the character
    appears in,
  - located_in_edges show the character's location per chapter,
  - when the character moves, the prior open edge gets until_chapter set
    and superseded_by_id chained to the new edge,
  - possesses_edges reflect a gain/loss,
  - status/knowledge deltas fold into character_states,
  - the materializer is the sole writer: stale snapshots whose source
    deltas disappeared do not survive a re-materialize,
  - re-running materialize is idempotent (no duplicates).

Each test owns its own novel (UUID-named) and deletes it in teardown so
parallel runs and reruns stay isolated.
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.db.client import DBClient
from pipeline.state.materializer import StateMaterializer


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seeded(db: DBClient):
    """Seed a small novel with 3 chapters, 2 characters, 2 locations, 1 object,
    and typed state_deltas rows telling a story of movement, a possession
    gain/loss, and a status/knowledge update.

    Story:
        ch 1: Aelric is at Fogwood Keep.
        ch 2: Aelric gains the silver dagger.
        ch 3: Aelric travels to Pellis Harbor (location change);
                Mira (other character) is at Pellis Harbor;
                Aelric loses the silver dagger; Aelric becomes wary;
                Aelric learns the harbor is watched.
    """
    novel_id = str(uuid.uuid4())
    title = f"TestNovel-{novel_id[:8]}"

    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO novels (id, title) VALUES (%s, %s)",
            (novel_id, title),
        )

        # 3 chapters
        chapter_ids: list[str] = []
        for n in range(1, 4):
            row = cur.execute(
                """
                INSERT INTO chapters (novel_id, number, raw_text)
                VALUES (%s, %s, %s) RETURNING id
                """,
                (novel_id, n, f"chapter {n} text"),
            )
            chapter_ids.append(str(cur.fetchone()[0]))

        # entities row first, then specialized rows
        def make_entity(kind: str, name: str) -> str:
            cur.execute(
                "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, kind, name),
            )
            return str(cur.fetchone()[0])

        aelric_eid = make_entity("character", "Aelric")
        mira_eid = make_entity("character", "Mira")
        keep_eid = make_entity("location", "Fogwood Keep")
        harbor_eid = make_entity("location", "Pellis Harbor")
        dagger_eid = make_entity("object", "Silver Dagger")

        cur.execute(
            "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, aelric_eid, "Aelric"),
        )
        aelric_id = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, mira_eid, "Mira"),
        )
        mira_id = str(cur.fetchone()[0])

        cur.execute(
            "INSERT INTO locations (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, keep_eid, "Fogwood Keep"),
        )
        keep_id = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO locations (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, harbor_eid, "Pellis Harbor"),
        )
        harbor_id = str(cur.fetchone()[0])

        cur.execute(
            "INSERT INTO objects (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, dagger_eid, "Silver Dagger"),
        )
        dagger_id = str(cur.fetchone()[0])

        # story as typed deltas:
        #   ch1: Aelric at Fogwood Keep (location)
        #   ch2: Aelric gains silver dagger (possession)
        #   ch3: Aelric moves to Pellis Harbor; Mira at Pellis Harbor;
        #        Aelric loses silver dagger; Aelric wary (status)
        ordinal_counter = {"n": 0}

        def add_delta(chapter_ix, kind, subject_eid, *, object_eid=None,
                      location_typed_id=None, change=None, attribute=None, detail=None):
            cur.execute(
                """
                INSERT INTO state_deltas (chapter_id, ordinal, kind, subject_id,
                                          object_id, location_id, change, attribute, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (chapter_ids[chapter_ix], ordinal_counter["n"], kind, subject_eid,
                 object_eid, location_typed_id, change, attribute, detail),
            )
            ordinal_counter["n"] += 1

        add_delta(0, "location", aelric_eid, location_typed_id=keep_id, change="move")
        add_delta(1, "possession", aelric_eid, object_eid=dagger_eid, change="gain")
        add_delta(2, "location", aelric_eid, location_typed_id=harbor_id, change="move")
        add_delta(2, "location", mira_eid, location_typed_id=harbor_id, change="move")
        add_delta(2, "possession", aelric_eid, object_eid=dagger_eid, change="loss")
        add_delta(2, "status", aelric_eid, change="update",
                  attribute="emotional_state", detail="wary")
        add_delta(2, "knowledge", aelric_eid, change="learn",
                  detail="the harbor is watched")

    yield {
        "novel_id": novel_id,
        "chapter_ids": chapter_ids,
        "aelric_id": aelric_id,
        "mira_id": mira_id,
        "aelric_char_id": aelric_id,
        "aelric_eid": aelric_eid,
        "mira_eid": mira_eid,
        "keep_id": keep_id,
        "harbor_id": harbor_id,
        "dagger_id": dagger_id,
    }

    # Teardown: cascading delete from novels.
    with db.transaction() as cur:
        cur.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def _fetch_state(db: DBClient, novel_id: str) -> list[dict]:
    return db.fetchall(
        """
        SELECT cs.character_id, c.name AS character_name, ch.number AS chapter_number,
               cs.location_id, cs.emotional_state
          FROM character_states cs
          JOIN characters c ON c.id = cs.character_id
          JOIN chapters ch ON ch.id = cs.chapter_id
         WHERE c.novel_id = %s
         ORDER BY c.name, ch.number
        """,
        (novel_id,),
        dict_rows=True,
    )


def _fetch_location_edges(db: DBClient, entity_id: str) -> list[dict]:
    return db.fetchall(
        """
        SELECT id, location_id, since_chapter, until_chapter, superseded_by_id
          FROM located_in_edges
         WHERE entity_id = %s
         ORDER BY since_chapter
        """,
        (entity_id,),
        dict_rows=True,
    )


def _fetch_possession_edges(db: DBClient, character_id: str) -> list[dict]:
    return db.fetchall(
        """
        SELECT id, object_id, since_chapter, until_chapter
          FROM possesses_edges
         WHERE character_id = %s
         ORDER BY since_chapter
        """,
        (character_id,),
        dict_rows=True,
    )


def test_materialize_writes_character_states(db, seeded):
    StateMaterializer(db).materialize(seeded["novel_id"], through_chapter=3)
    rows = _fetch_state(db, seeded["novel_id"])
    # Snapshots are only folded for chapters with a location/status/knowledge
    # delta touching the character. Chapter 2 only has a possession delta for
    # Aelric (no snapshot-affecting kind), so no row exists there.
    by_char = {(r["character_name"], r["chapter_number"]) for r in rows}
    assert ("Aelric", 1) in by_char
    assert ("Aelric", 2) not in by_char
    assert ("Aelric", 3) in by_char
    assert ("Mira", 3) in by_char


def test_materialize_chains_location_edges_on_move(db, seeded):
    StateMaterializer(db).materialize(seeded["novel_id"], through_chapter=3)
    edges = _fetch_location_edges(db, seeded["aelric_eid"])
    # Aelric was at the keep for chapters 1-2, then moves to the harbor in 3.
    assert len(edges) == 2

    first, second = edges
    assert str(first["location_id"]) == seeded["keep_id"]
    assert first["since_chapter"] == 1
    # First edge should have been closed when Aelric moved.
    assert first["until_chapter"] == 2  # since_chapter of next - 1
    assert first["superseded_by_id"] is not None
    assert str(first["superseded_by_id"]) == str(second["id"])

    assert str(second["location_id"]) == seeded["harbor_id"]
    assert second["since_chapter"] == 3
    assert second["until_chapter"] is None
    assert second["superseded_by_id"] is None


def test_materialize_records_object_pickup(db, seeded):
    StateMaterializer(db).materialize(seeded["novel_id"], through_chapter=3)
    edges = _fetch_possession_edges(db, seeded["aelric_id"])
    # Gained in chapter 2, lost in chapter 3: a single closed possession edge.
    assert len(edges) == 1
    edge = edges[0]
    assert str(edge["object_id"]) == seeded["dagger_id"]
    assert edge["since_chapter"] == 2
    assert edge["until_chapter"] == 3


def test_materialize_is_idempotent(db, seeded):
    mat = StateMaterializer(db)
    first = mat.materialize(seeded["novel_id"], through_chapter=3)
    second = mat.materialize(seeded["novel_id"], through_chapter=3)

    assert first.snapshots_written == second.snapshots_written
    assert first.location_edges_written == second.location_edges_written
    assert first.possession_edges_written == second.possession_edges_written

    # Rows did not duplicate.
    states = _fetch_state(db, seeded["novel_id"])
    aelric_loc_edges = _fetch_location_edges(db, seeded["aelric_eid"])
    aelric_obj_edges = _fetch_possession_edges(db, seeded["aelric_id"])
    assert len(states) == 3  # Aelric ch1, ch3 (2) + Mira ch3 (1)
    assert len(aelric_loc_edges) == 2
    assert len(aelric_obj_edges) == 1

    # And the run audit table picks up both runs.
    runs = db.fetchall(
        "SELECT through_chapter FROM materialized_state_runs WHERE novel_id = %s",
        (seeded["novel_id"],),
        dict_rows=True,
    )
    assert len(runs) == 2


def test_status_and_knowledge_fold_into_snapshots(db, seeded):
    StateMaterializer(db).materialize(seeded["novel_id"], 3)
    row = db.fetchone(
        """
        SELECT cs.emotional_state, cs.knowledge FROM character_states cs
          JOIN chapters ch ON ch.id = cs.chapter_id
         WHERE cs.character_id = %s AND ch.number = 3
        """,
        (seeded["aelric_char_id"],),
    )
    assert row[0] == "wary"
    assert "the harbor is watched" in (row[1] or [])


def test_materializer_is_sole_writer_and_prunes_stale_snapshots(db, seeded):
    StateMaterializer(db).materialize(seeded["novel_id"], 3)
    # A snapshot for a (character, chapter) pair with no surviving deltas must
    # not survive a re-materialize (novel-scoped rebuild).
    db.execute("DELETE FROM state_deltas WHERE detail = 'wary'")
    StateMaterializer(db).materialize(seeded["novel_id"], 3)
    row = db.fetchone(
        """
        SELECT cs.emotional_state FROM character_states cs
          JOIN chapters ch ON ch.id = cs.chapter_id
         WHERE cs.character_id = %s AND ch.number = 3
        """,
        (seeded["aelric_char_id"],),
    )
    assert row is None or row[0] is None
