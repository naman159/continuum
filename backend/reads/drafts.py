"""reads.drafts: the agent-draft review queue.

These are queue reads, not story reads: a parked draft is not canon and has
no point-in-time semantics, so unlike the rest of `reads/` these functions
take no `up_to_chapter` (see CUTOFF_EXEMPT in reads/tests/test_contract.py).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

_COLUMNS = """
    id, novel_id, chapter_number, title, raw_text, status, findings,
    submitted_at, resolved_at, resolution_note
"""


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    out["id"] = str(out["id"])
    out["novel_id"] = str(out["novel_id"])
    return out


def list_submissions(
    db: Any, novel_id: UUID | str, status: str = "pending"
) -> list[dict[str, Any]]:
    """Submissions for a novel, newest first. status='all' returns every row."""
    if status == "all":
        rows = db.fetchall(
            f"SELECT {_COLUMNS} FROM draft_submissions WHERE novel_id = %s"
            " ORDER BY submitted_at DESC",
            (str(novel_id),),
            dict_rows=True,
        )
    else:
        rows = db.fetchall(
            f"SELECT {_COLUMNS} FROM draft_submissions"
            " WHERE novel_id = %s AND status = %s ORDER BY submitted_at DESC",
            (str(novel_id), status),
            dict_rows=True,
        )
    return [_row(r) for r in rows]


def get_submission(db: Any, submission_id: UUID | str) -> dict[str, Any] | None:
    row = db.fetchone(
        f"SELECT {_COLUMNS} FROM draft_submissions WHERE id = %s",
        (str(submission_id),),
        dict_rows=True,
    )
    return _row(row) if row else None


def count_pending(db: Any, novel_id: UUID | str) -> int:
    """Badge count for the wiki nav."""
    return int(
        db.fetchval(
            "SELECT COUNT(*) FROM draft_submissions"
            " WHERE novel_id = %s AND status = 'pending'",
            (str(novel_id),),
        )
        or 0
    )
