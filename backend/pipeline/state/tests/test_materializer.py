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
            cur.execute(
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
         ORDER BY since_chapter, until_chapter NULLS LAST
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


# ----------------------------------------------------------------------
# Carry-forward semantics: location_id/goals/knowledge/physical_state/
# appearance persist into later snapshots even when the chapter's only
# delta is unrelated to them; emotional_state does NOT carry forward.


@pytest.fixture
def carry_forward_seeded(db: DBClient):
    """Seed a small 4-chapter, single-character novel that isolates the
    replay's carry-forward contract.

    Story:
        ch 1: Aelric at Fogwood Keep; goals/physical_state/appearance set
              via status deltas.
        ch 2: Aelric learns something -- a knowledge-only chapter (no
              status delta at all).
        ch 3: Aelric becomes wary -- a status delta sets emotional_state.
        ch 4: Aelric learns something else -- another knowledge-only
              chapter, coming *after* the emotional_state delta.
    """
    novel_id = str(uuid.uuid4())
    title = f"TestNovel-CarryForward-{novel_id[:8]}"

    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO novels (id, title) VALUES (%s, %s)",
            (novel_id, title),
        )

        chapter_ids: list[str] = []
        for n in range(1, 5):
            cur.execute(
                """
                INSERT INTO chapters (novel_id, number, raw_text)
                VALUES (%s, %s, %s) RETURNING id
                """,
                (novel_id, n, f"chapter {n} text"),
            )
            chapter_ids.append(str(cur.fetchone()[0]))

        def make_entity(kind: str, name: str) -> str:
            cur.execute(
                "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, kind, name),
            )
            return str(cur.fetchone()[0])

        aelric_eid = make_entity("character", "Aelric")
        keep_eid = make_entity("location", "Fogwood Keep")

        cur.execute(
            "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, aelric_eid, "Aelric"),
        )
        aelric_id = str(cur.fetchone()[0])

        cur.execute(
            "INSERT INTO locations (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, keep_eid, "Fogwood Keep"),
        )
        keep_id = str(cur.fetchone()[0])

        ordinal_counter = {"n": 0}

        def add_delta(chapter_ix, kind, subject_eid, *, location_typed_id=None,
                      change=None, attribute=None, detail=None):
            cur.execute(
                """
                INSERT INTO state_deltas (chapter_id, ordinal, kind, subject_id,
                                          location_id, change, attribute, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (chapter_ids[chapter_ix], ordinal_counter["n"], kind, subject_eid,
                 location_typed_id, change, attribute, detail),
            )
            ordinal_counter["n"] += 1

        # ch1: location + goals/physical_state/appearance set via status.
        add_delta(0, "location", aelric_eid, location_typed_id=keep_id, change="move")
        add_delta(0, "status", aelric_eid, attribute="goals", detail="reclaim the throne")
        add_delta(0, "status", aelric_eid, attribute="physical_state", detail="unharmed")
        add_delta(0, "status", aelric_eid, attribute="appearance", detail="travel-worn cloak")
        # ch2: knowledge-only chapter (no status delta at all).
        add_delta(1, "knowledge", aelric_eid, detail="the keep has a hidden passage")
        # ch3: emotional_state set via status delta.
        add_delta(2, "status", aelric_eid, attribute="emotional_state", detail="wary")
        # ch4: knowledge-only chapter, after the emotional_state delta.
        add_delta(3, "knowledge", aelric_eid, detail="the harbor is watched")

    yield {
        "novel_id": novel_id,
        "chapter_ids": chapter_ids,
        "aelric_id": aelric_id,
        "keep_id": keep_id,
    }

    with db.transaction() as cur:
        cur.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_carry_forward_semantics(db, carry_forward_seeded):
    seeded = carry_forward_seeded
    StateMaterializer(db).materialize(seeded["novel_id"], through_chapter=4)

    def _snapshot(chapter_number: int) -> dict:
        row = db.fetchone(
            """
            SELECT cs.location_id, cs.emotional_state, cs.goals, cs.knowledge,
                   cs.physical_state, cs.appearance
              FROM character_states cs
              JOIN chapters ch ON ch.id = cs.chapter_id
             WHERE cs.character_id = %s AND ch.number = %s
            """,
            (seeded["aelric_id"], chapter_number),
            dict_rows=True,
        )
        assert row is not None, f"expected a snapshot for chapter {chapter_number}"
        return row

    ch2 = _snapshot(2)
    # ch2's only delta is knowledge (non-status): location/goals/
    # physical_state/appearance must carry forward from ch1 even though
    # nothing in ch2 sets them.
    assert str(ch2["location_id"]) == seeded["keep_id"]
    assert ch2["goals"] == "reclaim the throne"
    assert ch2["physical_state"] == "unharmed"
    assert ch2["appearance"] == "travel-worn cloak"
    assert "the keep has a hidden passage" in (ch2["knowledge"] or [])
    # No status delta has ever touched emotional_state yet.
    assert ch2["emotional_state"] is None

    ch3 = _snapshot(3)
    assert ch3["emotional_state"] == "wary"

    ch4 = _snapshot(4)
    # ch4 comes after ch3's emotional_state="wary" status delta, but ch4's
    # only delta is knowledge (non-status): emotional_state must NOT carry
    # forward -- it resets to NULL because no status delta touched it in ch4.
    assert ch4["emotional_state"] is None
    # Meanwhile the carry-forward fields set back in ch1 are still intact.
    assert str(ch4["location_id"]) == seeded["keep_id"]
    assert ch4["goals"] == "reclaim the throne"
    assert ch4["physical_state"] == "unharmed"
    assert ch4["appearance"] == "travel-worn cloak"
    assert "the harbor is watched" in (ch4["knowledge"] or [])
    assert "the keep has a hidden passage" in (ch4["knowledge"] or [])


# ----------------------------------------------------------------------
# Same-chapter double move: two location deltas for one entity in the
# same chapter must not produce an inverted (since > until) interval.


@pytest.fixture
def double_move_seeded(db: DBClient):
    """Seed a 1-chapter novel where Aelric has two location deltas in the
    same chapter (e.g. two location deltas surfaced from separate chunks
    of per-chunk extraction): first the courtyard, then the tower.
    """
    novel_id = str(uuid.uuid4())
    title = f"TestNovel-DoubleMove-{novel_id[:8]}"

    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO novels (id, title) VALUES (%s, %s)",
            (novel_id, title),
        )

        cur.execute(
            """
            INSERT INTO chapters (novel_id, number, raw_text)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (novel_id, 1, "chapter 1 text"),
        )
        chapter_id = str(cur.fetchone()[0])

        def make_entity(kind: str, name: str) -> str:
            cur.execute(
                "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, kind, name),
            )
            return str(cur.fetchone()[0])

        aelric_eid = make_entity("character", "Aelric")
        courtyard_eid = make_entity("location", "Courtyard")
        tower_eid = make_entity("location", "Tower")

        cur.execute(
            "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, aelric_eid, "Aelric"),
        )

        cur.execute(
            "INSERT INTO locations (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, courtyard_eid, "Courtyard"),
        )
        courtyard_id = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO locations (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, tower_eid, "Tower"),
        )
        tower_id = str(cur.fetchone()[0])

        # Two location deltas for Aelric, same chapter, increasing ordinal.
        cur.execute(
            """
            INSERT INTO state_deltas (chapter_id, ordinal, kind, subject_id,
                                      location_id, change)
            VALUES (%s, %s, 'location', %s, %s, 'move')
            """,
            (chapter_id, 0, aelric_eid, courtyard_id),
        )
        cur.execute(
            """
            INSERT INTO state_deltas (chapter_id, ordinal, kind, subject_id,
                                      location_id, change)
            VALUES (%s, %s, 'location', %s, %s, 'move')
            """,
            (chapter_id, 1, aelric_eid, tower_id),
        )

    yield {
        "novel_id": novel_id,
        "aelric_eid": aelric_eid,
        "courtyard_id": courtyard_id,
        "tower_id": tower_id,
    }

    with db.transaction() as cur:
        cur.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_same_chapter_double_move_yields_valid_interval(db, double_move_seeded):
    seeded = double_move_seeded
    StateMaterializer(db).materialize(seeded["novel_id"], through_chapter=1)
    edges = _fetch_location_edges(db, seeded["aelric_eid"])
    assert len(edges) == 2

    first, second = edges
    assert str(first["location_id"]) == seeded["courtyard_id"]
    assert first["since_chapter"] == 1
    # A same-chapter supersession must yield a valid single-chapter interval
    # (since == until), not an inverted one (since > until) that
    # since<=N AND (until IS NULL OR until>=N) queries would silently drop.
    assert first["until_chapter"] == first["since_chapter"] == 1
    assert first["superseded_by_id"] is not None
    assert str(first["superseded_by_id"]) == str(second["id"])

    assert str(second["location_id"]) == seeded["tower_id"]
    assert second["since_chapter"] == 1
    assert second["until_chapter"] is None
    assert second["superseded_by_id"] is None


def test_historical_locations_and_loss_chapter_are_consistent(db, seeded):
    from reads.graphs import entity_graph
    from reads.knowledge import list_location_edges, list_possession_edges

    nid = seeded["novel_id"]
    StateMaterializer(db).materialize(nid)
    earlier = list_location_edges(db, nid, 1, True)
    aelric = next(r for r in earlier if str(r["entity_id"]) == seeded["aelric_eid"])
    assert str(aelric["location_id"]) == seeded["keep_id"]
    assert aelric["until_chapter"] is None  # future move is still hidden
    later = list_location_edges(db, nid, 3, True)
    assert str(next(r for r in later if str(r["entity_id"]) == seeded["aelric_eid"])["location_id"]) == seeded["harbor_id"]
    held = list_possession_edges(db, nid, 2, True)
    assert held and held[0]["until_chapter"] is None
    assert list_possession_edges(db, nid, 3, True) == []
    assert not any(e["edge_kind"] == "possession" for e in entity_graph(db, nid, 3)["edges"])


def test_current_materialization_resolves_horizon_after_waiting_for_lock(db, seeded):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from contextlib import contextmanager

    nid = seeded["novel_id"]
    waiting = Event()
    real_transaction = db.transaction

    class ObservedCursor:
        def __init__(self, cur):
            self.cur = cur

        def execute(self, query, params=None):
            if "pg_advisory_xact_lock" in query:
                waiting.set()
            return self.cur.execute(query, params)

        def __getattr__(self, name):
            return getattr(self.cur, name)

    class ObservedDB:
        @contextmanager
        def transaction(self):
            with real_transaction() as cur:
                yield ObservedCursor(cur)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with db.transaction() as blocker:
            blocker.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (nid,))
            future = executor.submit(StateMaterializer(ObservedDB()).materialize, nid)
            assert waiting.wait(timeout=5)
            # A chapter commits while the other materializer waits on its lock.
            db.execute("INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s, 4, 'next chapter')", (nid,))
        assert future.result(timeout=10).through_chapter == 4
