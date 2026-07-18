"""Integration test for the plot_threads upsert in _persist_extraction.

Real-DB test (same idiom as pipeline/critic/tests/test_critic.py): the
reopen semantics live in the ON CONFLICT SQL, so a fake can't cover them.
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.db.client import DBClient
from pipeline.extraction.resolver import EntityResolver
from pipeline.pipeline import _persist_extraction


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


def _seed(db: DBClient) -> tuple[str, str, str]:
    nid = str(uuid.uuid4())
    with db.transaction() as cur:
        cur.execute("INSERT INTO novels (id, title) VALUES (%s, %s)", (nid, f"UpsertTest-{nid[:8]}"))
        cur.execute(
            "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s,1,'ch1') RETURNING id",
            (nid,),
        )
        ch1 = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s,2,'ch2') RETURNING id",
            (nid,),
        )
        ch2 = str(cur.fetchone()[0])
    return nid, ch1, ch2


def _persist_thread(db: DBClient, novel_id: str, chapter_id: str, number: int, status: str) -> None:
    _persist_extraction(
        db,
        resolver=EntityResolver(db, novel_id=novel_id, chapter_number=number),
        chapter_id=chapter_id,
        chapter_number=number,
        extracted={"thread_updates": [{"title": "The Debt", "status": status}]},
    )


def test_reopening_a_thread_clears_the_stale_closed_chapter(db):
    novel_id, ch1, ch2 = _seed(db)
    try:
        _persist_thread(db, novel_id, ch1, 1, "closed")
        row = db.fetchone(
            "SELECT status, closed_chapter FROM plot_threads WHERE novel_id = %s AND title = %s",
            (novel_id, "The Debt"),
            dict_rows=True,
        )
        assert (row["status"], row["closed_chapter"]) == ("closed", 1)

        # A later chapter reopens the thread: the stale anchor must be cleared,
        # or every capped read >= 1 reports the live thread as closed forever.
        _persist_thread(db, novel_id, ch2, 2, "progressing")
        row = db.fetchone(
            "SELECT status, closed_chapter FROM plot_threads WHERE novel_id = %s AND title = %s",
            (novel_id, "The Debt"),
            dict_rows=True,
        )
        assert row["status"] == "progressing"
        assert row["closed_chapter"] is None
    finally:
        db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))
