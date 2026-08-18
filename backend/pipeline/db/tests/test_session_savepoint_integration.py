"""Real-Postgres coverage for DBSession.savepoint.

The persist_extras helpers run inside the single transaction opened by
analyze_chapter and catch per-row failures so one bad row doesn't lose a
chapter. That only works if the failed statement is rolled back to a
SAVEPOINT: Postgres aborts the whole transaction on any statement error, so
without one, every later statement raises InFailedSqlTransaction and the
final COMMIT is silently downgraded to ROLLBACK — losing the chapter while
reporting success.

Fake-DB tests cannot catch this; it is a property of the real connection's
transaction state machine.
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.db.client import DBClient


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
        (f"savepoint-it-{uuid.uuid4()}",), commit=True,
    ))
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_swallowed_error_in_savepoint_leaves_transaction_usable(db, novel):
    """A caught failure inside a savepoint must not poison later statements."""
    with db.session() as s:
        s.execute(
            "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s,%s,%s)",
            (novel, 1, "before the failure"),
        )

        with pytest.raises(Exception):
            with s.savepoint():
                s.fetchval("SELECT 1/0")

        # The whole point: the connection is still usable afterwards.
        assert s.fetchval("SELECT 42") == 42
        s.execute(
            "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s,%s,%s)",
            (novel, 2, "after the failure"),
        )

    # And the outer transaction really committed both rows.
    rows = db.fetchall(
        "SELECT number FROM chapters WHERE novel_id = %s ORDER BY number", (novel,)
    )
    assert [r[0] for r in rows] == [1, 2]


def test_without_savepoint_a_swallowed_error_silently_discards_the_work(db, novel):
    """Characterization of the bug this fix exists to prevent.

    Swallowing an error with no savepoint and then committing does not raise
    — Postgres converts the COMMIT of an aborted transaction to a ROLLBACK.
    This test documents that trap so nobody reintroduces the pattern.
    """
    with db.session() as s:
        s.execute(
            "INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s,%s,%s)",
            (novel, 7, "doomed"),
        )
        try:
            s.fetchval("SELECT 1/0")
        except Exception:
            pass  # exactly what persist_extras used to do

    # session() exited without raising, so the caller believes it committed.
    assert db.fetchval(
        "SELECT count(*) FROM chapters WHERE novel_id = %s", (novel,)
    ) == 0
