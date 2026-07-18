"""Tests for the Continuity Critic.

Each test seeds a minimal novel + relevant rows for the check it exercises,
runs the critic against a synthetic DraftChapter, and asserts findings.
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.critic import ContinuityCritic
from pipeline.critic.types import DraftChapter, Severity
from pipeline.db.client import DBClient


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


def _make_novel(db: DBClient) -> str:
    nid = str(uuid.uuid4())
    db.execute(
        "INSERT INTO novels (id, title) VALUES (%s, %s)",
        (nid, f"CritTest-{nid[:8]}"),
    )
    return nid


def _make_chapter(db: DBClient, novel_id: str, number: int) -> str:
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, number, f"ch{number}"),
        )
        return str(cur.fetchone()[0])


def _make_entity(db: DBClient, novel_id: str, kind: str, name: str) -> str:
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, kind, name),
        )
        return str(cur.fetchone()[0])


def _cleanup(db: DBClient, novel_id: str) -> None:
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


# ---------------------------------------------------------------------------
# entity_mention


def test_entity_mention_flags_locked_canon_violation(db):
    novel_id = _make_novel(db)
    try:
        eid = _make_entity(db, novel_id, "character", "Aelric")
        db.execute(
            """
            INSERT INTO canon_facts
                (novel_id, kind, subject_entity_id, predicate, value, locked)
            VALUES (%s, 'identity', %s, 'eye_color', 'green', true)
            """,
            (novel_id, eid),
        )
        draft = DraftChapter(
            novel_id=novel_id,
            chapter_number=2,
            text="...",
            mentions=[{
                "entity_id": eid,
                "predicate": "eye_color",
                "claimed_value": "blue",
                "quote": "his blue eyes",
            }],
        )
        report = ContinuityCritic(db).critique(draft)
        assert not report.passed
        fails = [f for f in report.findings if f.check == "entity_mention"]
        assert len(fails) == 1
        assert fails[0].severity == Severity.FAIL
        assert "canon" in fails[0].message
    finally:
        _cleanup(db, novel_id)


def test_entity_mention_warns_when_unlocked(db):
    novel_id = _make_novel(db)
    try:
        eid = _make_entity(db, novel_id, "character", "Aelric")
        db.execute(
            """
            INSERT INTO canon_facts
                (novel_id, kind, subject_entity_id, predicate, value, locked)
            VALUES (%s, 'identity', %s, 'mood', 'somber', false)
            """,
            (novel_id, eid),
        )
        draft = DraftChapter(
            novel_id=novel_id,
            chapter_number=2,
            text="...",
            mentions=[{
                "entity_id": eid,
                "predicate": "mood",
                "claimed_value": "joyful",
                "quote": "she laughed brightly",
            }],
        )
        report = ContinuityCritic(db).critique(draft)
        # No FAILs because the fact wasn't locked, but should be a WARN.
        assert report.passed
        warns = [f for f in report.warns if f.check == "entity_mention"]
        assert len(warns) == 1
    finally:
        _cleanup(db, novel_id)


# ---------------------------------------------------------------------------
# location/possession


def test_location_check_flags_two_places_at_once(db):
    novel_id = _make_novel(db)
    try:
        ch_id = _make_chapter(db, novel_id, 1)
        char_eid = _make_entity(db, novel_id, "character", "Mira")
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, char_eid, "Mira"),
            )
            mira_id = str(cur.fetchone()[0])
        loc_a_eid = _make_entity(db, novel_id, "location", "Keep")
        loc_b_eid = _make_entity(db, novel_id, "location", "Harbor")
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO locations (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, loc_a_eid, "Keep"),
            )
            loc_a = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO locations (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, loc_b_eid, "Harbor"),
            )
            loc_b = str(cur.fetchone()[0])
        draft = DraftChapter(
            novel_id=novel_id,
            chapter_number=2,
            text="...",
            location_claims=[
                {"character_id": mira_id, "location_id": loc_a, "quote": "Mira in the keep"},
                {"character_id": mira_id, "location_id": loc_b, "quote": "Mira at the harbor"},
            ],
        )
        report = ContinuityCritic(db).critique(draft)
        assert not report.passed
        finding = next(f for f in report.fails if f.check == "location_possession")
        assert "2 locations" in finding.message
        _ = ch_id  # quiet linter
    finally:
        _cleanup(db, novel_id)


def test_possession_check_warns_when_object_not_held(db):
    novel_id = _make_novel(db)
    try:
        char_eid = _make_entity(db, novel_id, "character", "Aelric")
        obj_eid = _make_entity(db, novel_id, "object", "Dagger")
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, char_eid, "Aelric"),
            )
            ael_id = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO objects (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, obj_eid, "Dagger"),
            )
            dag_id = str(cur.fetchone()[0])
        draft = DraftChapter(
            novel_id=novel_id,
            chapter_number=3,
            text="...",
            possession_claims=[
                {"character_id": ael_id, "object_id": dag_id, "quote": "drew his dagger"},
            ],
        )
        report = ContinuityCritic(db).critique(draft)
        # Possession warn (not FAIL) — chapter could be introducing the pickup.
        warn = next(f for f in report.warns if f.check == "location_possession")
        assert "possession edge" in warn.message
    finally:
        _cleanup(db, novel_id)


def test_possession_check_warns_when_object_lost_in_previous_chapter(db):
    novel_id = _make_novel(db)
    try:
        char_eid = _make_entity(db, novel_id, "character", "Aelric")
        obj_eid = _make_entity(db, novel_id, "object", "Dagger")
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, char_eid, "Aelric"),
            )
            ael_id = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO objects (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, obj_eid, "Dagger"),
            )
            dag_id = str(cur.fetchone()[0])
            # Held since ch 1, lost during ch 2 (replay closes the edge at the
            # loss chapter) — so it is NOT held entering ch 3.
            cur.execute(
                "INSERT INTO possesses_edges (character_id, object_id, since_chapter, until_chapter)"
                " VALUES (%s,%s,%s,%s)",
                (ael_id, dag_id, 1, 2),
            )
        draft = DraftChapter(
            novel_id=novel_id,
            chapter_number=3,
            text="...",
            possession_claims=[
                {"character_id": ael_id, "object_id": dag_id, "quote": "drew his dagger"},
            ],
        )
        report = ContinuityCritic(db).critique(draft)
        warn = next(f for f in report.warns if f.check == "location_possession")
        assert "possession edge" in warn.message
    finally:
        _cleanup(db, novel_id)


# ---------------------------------------------------------------------------
# knowledge_state


def test_knowledge_state_flags_unsupported_claim(db):
    novel_id = _make_novel(db)
    try:
        char_eid = _make_entity(db, novel_id, "character", "Aelric")
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, char_eid, "Aelric"),
            )
            ael_id = str(cur.fetchone()[0])
        # No knows_edges seeded — Aelric is acting on knowledge he doesn't have.
        draft = DraftChapter(
            novel_id=novel_id,
            chapter_number=5,
            text="...",
            knowledge_claims=[{
                "character_id": ael_id,
                "fact_description": "Mira is his sister",
                "source_type": "inference",
                "learned_this_chapter": False,
                "quote": "he knew Mira was his sister",
            }],
        )
        report = ContinuityCritic(db).critique(draft)
        assert not report.passed
        fails = [f for f in report.fails if f.check == "knowledge_state"]
        assert len(fails) == 1
        assert "mira is his sister" in fails[0].message.lower()


    finally:
        _cleanup(db, novel_id)


def test_knowledge_state_accepts_learning_this_chapter(db):
    novel_id = _make_novel(db)
    try:
        char_eid = _make_entity(db, novel_id, "character", "Aelric")
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, char_eid, "Aelric"),
            )
            ael_id = str(cur.fetchone()[0])
        draft = DraftChapter(
            novel_id=novel_id,
            chapter_number=5,
            text="...",
            knowledge_claims=[{
                "character_id": ael_id,
                "fact_description": "Mira is his sister",
                "source_type": "told",
                "learned_this_chapter": True,
                "quote": "'Mira, you are my sister,' Aelric realized as she spoke",
            }],
        )
        report = ContinuityCritic(db).critique(draft)
        assert report.passed
    finally:
        _cleanup(db, novel_id)


def test_knowledge_state_passes_when_prior_edge_exists(db):
    novel_id = _make_novel(db)
    try:
        char_eid = _make_entity(db, novel_id, "character", "Aelric")
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO characters (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, char_eid, "Aelric"),
            )
            ael_id = str(cur.fetchone()[0])
        # Seed knows_edges.
        db.execute(
            """
            INSERT INTO knows_edges
                (character_id, fact_description, learned_chapter, source_type, certainty)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (ael_id, "Mira is his sister", 3, "told", 0.95),
        )
        draft = DraftChapter(
            novel_id=novel_id,
            chapter_number=5,
            text="...",
            knowledge_claims=[{
                "character_id": ael_id,
                "fact_description": "Mira is his sister",
                "source_type": "inference",
                "learned_this_chapter": False,
            }],
        )
        report = ContinuityCritic(db).critique(draft)
        assert report.passed
    finally:
        _cleanup(db, novel_id)


# ---------------------------------------------------------------------------
# commitment


def test_commitment_warns_on_unkept_planned_commitment(db):
    novel_id = _make_novel(db)
    try:
        with db.transaction() as cur:
            cur.execute(
                """
                INSERT INTO commitments
                    (novel_id, foreshadow_text, foreshadow_chapter, status)
                VALUES (%s, %s, %s, 'pending') RETURNING id
                """,
                (novel_id, "a sword glints in the firelight", 2),
            )
            cid = str(cur.fetchone()[0])
        draft = DraftChapter(
            novel_id=novel_id,
            chapter_number=4,
            text="...",
            planned_commitment_ids=[cid],
        )
        report = ContinuityCritic(db).critique(draft)
        warns = [f for f in report.warns if f.check == "commitments"]
        assert len(warns) >= 1
    finally:
        _cleanup(db, novel_id)


# ---------------------------------------------------------------------------
# thread_coverage


def test_thread_coverage_warns_when_thread_not_touched(db):
    novel_id = _make_novel(db)
    try:
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO plot_threads (novel_id, title) VALUES (%s, %s) RETURNING id",
                (novel_id, "Aelric's vengeance"),
            )
            tid = str(cur.fetchone()[0])
        draft = DraftChapter(
            novel_id=novel_id,
            chapter_number=3,
            text="...",
            planned_thread_ids=[tid],
            events=[],
        )
        report = ContinuityCritic(db).critique(draft)
        warns = [f for f in report.warns if f.check == "thread_coverage"]
        assert len(warns) == 1
        assert tid in warns[0].message
    finally:
        _cleanup(db, novel_id)
