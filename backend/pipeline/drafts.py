"""Human adjudication of parked agent drafts.

`accept_submission` is the only caller allowed to set `_gate_bypass` on
analyze_chapter. Accepting does not erase the findings that blocked the draft:
they are written through to `continuity_flags` so a human-blessed
contradiction stays visible in the wiki instead of being silently absolved.
"""

from __future__ import annotations

from typing import Any

from pipeline.config import settings
from pipeline.pipeline import analyze_chapter
from reads.drafts import get_submission


def _load_pending(db: Any, submission_id: str) -> dict[str, Any]:
    row = get_submission(db, submission_id)
    if row is None:
        raise ValueError(f"draft submission {submission_id} not found")
    if row["status"] != "pending":
        raise ValueError(
            f"draft submission {submission_id} is already resolved "
            f"(status={row['status']})"
        )
    return row


def _write_findings_through(db: Any, chapter_id: str, findings: dict[str, Any]) -> int:
    """Record the blocking findings against the accepted chapter."""
    rows = [
        (
            chapter_id,
            (f"[accepted despite continuity FAIL] {f.get('message', '')}"
             + (f" — quote: {f['quote']}" if f.get("quote") else "")),
            f"accepted_override:{f.get('check', 'unknown')}",
        )
        for f in findings.get("fails", [])
    ]
    if not rows:
        return 0
    db.execute_many(
        "INSERT INTO continuity_flags (chapter_id, description, flag_type)"
        " VALUES (%s, %s, %s)",
        rows,
    )
    return len(rows)


def accept_submission(
    db: Any,
    submission_id: str,
    *,
    note: str | None = None,
    edited_text: str | None = None,
) -> dict[str, Any]:
    """Ingest a parked draft as canon, bypassing the gate (human override)."""
    row = _load_pending(db, submission_id)
    text = edited_text if edited_text is not None else row["raw_text"]
    resolution_note = note
    if edited_text is not None:
        resolution_note = f"{note or 'accepted'} (edited before ingest)"

    outcome = analyze_chapter(
        novel_id=row["novel_id"],
        chapter_number=row["chapter_number"],
        raw_text=text,
        chapter_title=row["title"],
        use_mock_llm=None,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        db=db,
        replace=False,
        source="agent",
        _gate_bypass=True,
    )
    chapter_id = str(outcome["chapter_id"])
    flags_written = _write_findings_through(db, chapter_id, row["findings"] or {})

    db.execute(
        """
        UPDATE draft_submissions
           SET status = 'accepted', resolved_at = now(),
               resolution_note = %s, raw_text = %s
         WHERE id = %s
        """,
        (resolution_note, text, submission_id),
    )
    return {
        "accepted": True,
        "chapter_id": chapter_id,
        "flags_written": flags_written,
    }


def reject_submission(
    db: Any, submission_id: str, *, note: str | None = None
) -> dict[str, Any]:
    """Mark a parked draft rejected. The agent must resubmit a revision."""
    _load_pending(db, submission_id)
    db.execute(
        """
        UPDATE draft_submissions
           SET status = 'rejected', resolved_at = now(), resolution_note = %s
         WHERE id = %s
        """,
        (note, submission_id),
    )
    return {"rejected": True, "submission_id": str(submission_id)}
