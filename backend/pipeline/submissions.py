"""Writes to the agent-draft review queue (`draft_submissions`).

Persistence only — nothing here decides whether a draft is acceptable. The
judgement is `pipeline.critic.service.critique_draft`; the policy is
`analyze_chapter(on_continuity_fail=...)`. This module just records the
outcome so a refused draft is waiting for a human instead of lost.

Lives apart from `pipeline.drafts` (which adjudicates parked drafts) because
`analyze_chapter` needs to park and `pipeline.drafts` calls `analyze_chapter`.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["park_draft", "supersede_pending"]


def _supersede_pending(cur: Any, *, novel_id: str, chapter_number: int, note: str) -> None:
    """Mark pending rows rejected using the supplied execute interface.

    A cursor or DBSession keeps this write inside the caller's transaction.
    """
    cur.execute(
        """
        UPDATE draft_submissions
           SET status = 'rejected',
               resolved_at = now(),
               resolution_note = %s
         WHERE novel_id = %s AND chapter_number = %s AND status = 'pending'
        """,
        (note, str(novel_id), chapter_number),
    )


def park_draft(
    db: Any,
    *,
    novel_id: str,
    chapter_number: int,
    title: str | None,
    raw_text: str,
    findings: dict[str, Any],
) -> str:
    """Supersede any pending row for this chapter, then park a new one.

    Both statements run in ONE transaction: superseding the old row without
    parking the new one would drop the author's draft on the floor.

    Note `DBClient.fetchval` defaults to commit=False and ROLLS BACK, so an
    `INSERT ... RETURNING` through it silently discards the row. `transaction()`
    commits on success, which is why the insert goes through its cursor.

    Not wrapped in try/except: refusing a write without recording the draft
    would lose the author's work, so a failed park is a hard error.
    """
    with db.transaction() as cur:
        _supersede_pending(
            cur,
            novel_id=novel_id,
            chapter_number=chapter_number,
            note="superseded by resubmission",
        )
        cur.execute(
            """
            INSERT INTO draft_submissions
                (novel_id, chapter_number, title, raw_text, status, findings)
            VALUES (%s, %s, %s, %s, 'pending', %s::jsonb)
            RETURNING id
            """,
            (str(novel_id), chapter_number, title, raw_text, json.dumps(findings)),
        )
        return str(cur.fetchone()[0])


def supersede_pending(db: Any, *, novel_id: str, chapter_number: int, note: str) -> None:
    """Clear the stale pending row left by an earlier failing draft for this
    chapter — otherwise the queue holds a 'pending' row for text the author has
    since fixed, and a reviewer who opens it hits the duplicate-chapter 409 the
    moment they try to accept. Nothing new is parked on this path."""
    # DBClient.execute commits; DBSession.execute joins the chapter transaction.
    _supersede_pending(
        db, novel_id=novel_id, chapter_number=chapter_number, note=note
    )
