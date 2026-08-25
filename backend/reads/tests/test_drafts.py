"""Queue reads over draft_submissions."""

from __future__ import annotations

import json

from pipeline.db.client import DBClient
from reads import drafts as drafts_reads


def _park(db: DBClient, novel_id: str, number: int, status: str = "pending") -> str:
    return str(
        db.fetchval(
            """
            INSERT INTO draft_submissions
                (novel_id, chapter_number, title, raw_text, status, findings)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (novel_id, number, f"Ch {number}", "draft text", status,
             json.dumps({"fails": [], "warns": []})),
            commit=True,
        )
    )


def test_list_submissions_returns_only_pending_by_default(db, seed_novel):
    seeded = seed_novel(db)
    novel_id = seeded["novel_id"]
    pending_id = _park(db, novel_id, 90)
    _park(db, novel_id, 91, status="rejected")

    rows = drafts_reads.list_submissions(db, novel_id)

    assert [r["id"] for r in rows] == [pending_id]
    assert rows[0]["chapter_number"] == 90
    assert rows[0]["findings"] == {"fails": [], "warns": []}


def test_list_submissions_can_filter_to_all(db, seed_novel):
    seeded = seed_novel(db)
    novel_id = seeded["novel_id"]
    _park(db, novel_id, 90)
    _park(db, novel_id, 91, status="rejected")

    rows = drafts_reads.list_submissions(db, novel_id, status="all")

    assert {r["chapter_number"] for r in rows} == {90, 91}


def test_get_submission_returns_row_and_none_for_missing(db, seed_novel):
    seeded = seed_novel(db)
    submission_id = _park(db, seeded["novel_id"], 90)

    row = drafts_reads.get_submission(db, submission_id)

    assert row is not None
    assert row["raw_text"] == "draft text"
    assert drafts_reads.get_submission(
        db, "00000000-0000-0000-0000-000000000000"
    ) is None
