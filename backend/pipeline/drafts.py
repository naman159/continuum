"""Human adjudication of parked agent drafts.

Accepting re-ingests the draft with `on_continuity_fail="warn"` — the reviewer
IS the review step, so a finding here is information rather than a veto.
Accepting does not erase the findings that blocked the draft: they are written
through to `continuity_flags` so a human-blessed contradiction stays visible in
the wiki instead of being silently absolved.
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


def _write_findings_through(
    cur: Any, chapter_id: str, findings: dict[str, Any], *, edited: bool
) -> int:
    """Record the blocking findings against the accepted chapter.

    Takes a cursor or DBSession so these writes share the chapter transaction.

    `edited` reflects whether the human changed the text before accepting —
    NOT whether the edit actually fixed anything. The re-ingest runs under
    "warn", so it does produce a critique of the edited text, but that report
    lands in `critique_reports`; it never re-adjudicates the blocking verdict
    recorded here. So we can't claim these findings no longer apply. Stamping
    the original "[accepted despite continuity FAIL]" label on text the
    reviewer specifically edited — the most likely reason to edit at all —
    would falsely imply the contradiction is still there. The edited-case
    label says only what we actually know: the *original* draft FAILed; this
    text may or may not still have that problem.
    """
    prefix = (
        "[accepted after edit; original draft FAILed]"
        if edited
        else "[accepted despite continuity FAIL]"
    )
    rows = [
        (
            chapter_id,
            (f"{prefix} {f.get('message', '')}"
             + (f" — quote: {f['quote']}" if f.get("quote") else "")),
            f"accepted_override:{f.get('check', 'unknown')}",
        )
        for f in findings.get("fails", [])
    ]
    for row in rows:
        cur.execute(
            "INSERT INTO continuity_flags (chapter_id, description, flag_type)"
            " VALUES (%s, %s, %s)",
            row,
        )
    return len(rows)


def accept_submission(
    db: Any,
    submission_id: str,
    *,
    note: str | None = None,
    edited_text: str | None = None,
) -> dict[str, Any]:
    """Ingest a parked draft as canon under "warn" policy (human override).

    The chapter, original findings, and review status commit together through
    analyze_chapter's persistence hook. A failed status write or a concurrent
    rejection rolls back the chapter too; the pending draft remains retryable
    after a database failure. The conditional UPDATE prevents a stale reviewer
    from accepting a submission another reviewer has already resolved.
    """
    row = _load_pending(db, submission_id)
    text = edited_text if edited_text is not None else row["raw_text"]
    resolution_note = note
    if edited_text is not None:
        resolution_note = f"{note or 'accepted'} (edited before ingest)"
    if not text.strip():
        raise ValueError("accepted chapter text must not be empty")

    flags_written = 0

    def finalize(session, chapter_id: str) -> None:
        nonlocal flags_written
        resolved = session.fetchval(
            """
            UPDATE draft_submissions
               SET status = 'accepted', resolved_at = now(),
                   resolution_note = %s, raw_text = %s
             WHERE id = %s AND status = 'pending'
            RETURNING id
            """,
            (resolution_note, text, submission_id),
        )
        if resolved is None:
            raise ValueError(f"draft submission {submission_id} is already resolved")
        flags_written = _write_findings_through(
            session, chapter_id, row["findings"] or {}, edited=edited_text is not None
        )

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
        on_continuity_fail="warn",
        on_persist=finalize,
    )
    chapter_id = str(outcome["chapter_id"])

    return {
        "accepted": True,
        "chapter_id": chapter_id,
        "flags_written": flags_written,
    }


def reject_submission(
    db: Any, submission_id: str, *, note: str | None = None
) -> dict[str, Any]:
    """Mark a parked draft rejected. The agent must resubmit a revision.

    The UPDATE is a compare-and-swap (`AND status = 'pending'`), matching
    accept_submission: `_load_pending` above is a bare read, so a concurrent
    accept can resolve the submission between that read and this write. The
    CAS makes this reject a no-op (raising, not overwriting) if that happens,
    instead of flipping an already-accepted, now-canon submission back to
    'rejected'.
    """
    _load_pending(db, submission_id)
    with db.transaction() as cur:
        cur.execute(
            """
            UPDATE draft_submissions
               SET status = 'rejected', resolved_at = now(), resolution_note = %s
             WHERE id = %s AND status = 'pending'
            """,
            (note, submission_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"draft submission {submission_id} is already resolved")
    return {"rejected": True, "submission_id": str(submission_id)}
