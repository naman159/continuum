"""Accepting and rejecting parked drafts."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from pipeline import drafts as drafts_mod
from reads import drafts as drafts_reads
from testing.drafts import _force_mock_llm  # noqa: F401  (autouse fixture)


def _park(db, novel_id: str, number: int = 90, text: str = "Elara walked in.") -> str:
    findings = {
        "fails": [{"check": "knowledge_state", "severity": "FAIL",
                   "message": "Elara knows something she shouldn't",
                   "quote": "She already knew.", "suggested_fix": None, "context": {}}],
        "warns": [],
    }
    return str(
        db.fetchval(
            """
            INSERT INTO draft_submissions
                (novel_id, chapter_number, title, raw_text, status, findings)
            VALUES (%s, %s, 'Ch 90', %s, 'pending', %s::jsonb)
            RETURNING id
            """,
            (novel_id, number, text, json.dumps(findings)),
            commit=True,
        )
    )


def test_accept_ingests_the_draft_and_marks_it_accepted(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    result = drafts_mod.accept_submission(db, submission_id, note="deliberate retcon")

    assert result["accepted"] is True
    assert result["chapter_id"]

    row = drafts_reads.get_submission(db, submission_id)
    assert row["status"] == "accepted"
    assert row["resolution_note"] == "deliberate retcon"
    assert row["resolved_at"] is not None

    assert int(
        db.fetchval(
            "SELECT COUNT(*) FROM chapters WHERE novel_id = %s AND number = 90",
            (novel_id,),
        )
    ) == 1


def test_accept_writes_findings_through_to_continuity_flags(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    result = drafts_mod.accept_submission(db, submission_id, note=None)

    assert result["flags_written"] == 1
    flags = db.fetchall(
        "SELECT description, flag_type FROM continuity_flags WHERE chapter_id = %s",
        (result["chapter_id"],),
        dict_rows=True,
    )
    assert len(flags) == 1
    assert flags[0]["flag_type"] == "accepted_override:knowledge_state"
    assert "Elara knows something she shouldn't" in flags[0]["description"]


def test_accept_with_edits_ingests_the_edited_text(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id, text="original text")

    result = drafts_mod.accept_submission(
        db, submission_id, note="fixed the timeline", edited_text="Kael walked in."
    )

    stored = db.fetchval(
        "SELECT raw_text FROM chapters WHERE id = %s", (result["chapter_id"],)
    )
    assert stored == "Kael walked in."
    row = drafts_reads.get_submission(db, submission_id)
    assert row["raw_text"] == "Kael walked in."
    assert "edited" in row["resolution_note"]


def test_accept_with_edits_labels_flags_as_edited_not_still_failing(db, seed_novel):
    """Editing before accepting is most often an attempt to fix the flagged
    contradiction. The write-through must not claim the (unre-critiqued)
    edited text still has the problem — it should only say the original
    draft failed."""
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id, text="original text")

    result = drafts_mod.accept_submission(
        db, submission_id, note="fixed it", edited_text="Kael walked in."
    )

    flags = db.fetchall(
        "SELECT description FROM continuity_flags WHERE chapter_id = %s",
        (result["chapter_id"],),
        dict_rows=True,
    )
    assert len(flags) == 1
    assert flags[0]["description"].startswith(
        "[accepted after edit; original draft FAILed]"
    )
    assert "[accepted despite continuity FAIL]" not in flags[0]["description"]


def test_accept_without_edits_still_uses_the_despite_fail_label(db, seed_novel):
    """The un-edited path is unchanged: the reviewer accepted the draft
    exactly as submitted, so the original label — the chapter as accepted
    still has the flagged problem — is accurate."""
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    result = drafts_mod.accept_submission(db, submission_id, note="deliberate retcon")

    flags = db.fetchall(
        "SELECT description FROM continuity_flags WHERE chapter_id = %s",
        (result["chapter_id"],),
        dict_rows=True,
    )
    assert len(flags) == 1
    assert flags[0]["description"].startswith("[accepted despite continuity FAIL]")


def test_accept_rolls_back_flags_if_the_status_update_fails(db, seed_novel, monkeypatch):
    """The findings write-through and the status UPDATE are one transaction:
    if the UPDATE fails, the INSERTs into continuity_flags must not survive
    either, even though the chapter itself (committed inside analyze_chapter,
    which cannot be joined to this transaction) does.
    """
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    real_transaction = db.transaction

    class _RaisingCursor:
        def __init__(self, real_cur):
            self._real = real_cur

        def execute(self, query, params=None):
            if "UPDATE draft_submissions" in query:
                raise RuntimeError("boom: status update failed")
            return self._real.execute(query, params)

        def __getattr__(self, name):
            return getattr(self._real, name)

    @contextmanager
    def _boom_transaction():
        with real_transaction() as cur:
            yield _RaisingCursor(cur)

    monkeypatch.setattr(db, "transaction", _boom_transaction)

    with pytest.raises(RuntimeError, match="still 'pending'") as excinfo:
        drafts_mod.accept_submission(db, submission_id, note="deliberate retcon")

    assert submission_id in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert "boom: status update failed" in str(excinfo.value.__cause__)

    chapter_id = db.fetchval(
        "SELECT id FROM chapters WHERE novel_id = %s AND number = 90", (novel_id,)
    )
    assert chapter_id is not None  # analyze_chapter's own commit isn't rolled back

    flags = db.fetchall(
        "SELECT id FROM continuity_flags WHERE chapter_id = %s", (str(chapter_id),)
    )
    assert flags == []  # rolled back together with the failed status update

    row = drafts_reads.get_submission(db, submission_id)
    assert row["status"] == "pending"  # never got marked accepted


def test_reject_marks_rejected_and_writes_no_chapter(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)

    result = drafts_mod.reject_submission(db, submission_id, note="off voice")

    assert result["rejected"] is True
    row = drafts_reads.get_submission(db, submission_id)
    assert row["status"] == "rejected"
    assert row["resolution_note"] == "off voice"
    assert int(
        db.fetchval(
            "SELECT COUNT(*) FROM chapters WHERE novel_id = %s AND number = 90",
            (novel_id,),
        )
    ) == 0


def test_reject_loses_a_race_to_a_concurrent_accept(db, seed_novel, monkeypatch):
    """Two reviewers can both pass `_load_pending`'s read before either
    writes. Simulate that by having the reject call operate on a stale
    'pending' snapshot (captured before a concurrent accept actually
    resolves the row) — the CAS on the UPDATE itself, not the earlier read,
    must be what stops the second writer from clobbering the first one's
    resolution."""
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)
    stale_row = drafts_mod._load_pending(db, submission_id)

    drafts_mod.accept_submission(db, submission_id, note="reviewer A accepts first")

    monkeypatch.setattr(drafts_mod, "_load_pending", lambda db, sid: stale_row)
    with pytest.raises(ValueError, match="already resolved"):
        drafts_mod.reject_submission(db, submission_id, note="reviewer B (stale) rejects")

    # The winner's resolution must survive untouched.
    row = drafts_reads.get_submission(db, submission_id)
    assert row["status"] == "accepted"
    assert row["resolution_note"] == "reviewer A accepts first"


def test_accept_loses_a_race_to_a_concurrent_reject(db, seed_novel, monkeypatch):
    """Mirror of the above: a stale-read accept must not flip an
    already-rejected submission back to 'accepted', even though the chapter
    it ingests (analyze_chapter's own commit, which cannot be joined to this
    transaction) cannot be un-ingested at this point."""
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)
    stale_row = drafts_mod._load_pending(db, submission_id)

    drafts_mod.reject_submission(db, submission_id, note="reviewer A rejects first")

    monkeypatch.setattr(drafts_mod, "_load_pending", lambda db, sid: stale_row)
    with pytest.raises(ValueError, match="already resolved"):
        drafts_mod.accept_submission(db, submission_id, note="reviewer B (stale) accepts")

    row = drafts_reads.get_submission(db, submission_id)
    assert row["status"] == "rejected"
    assert row["resolution_note"] == "reviewer A rejects first"


def test_resolving_a_missing_or_resolved_submission_raises(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)
    drafts_mod.reject_submission(db, submission_id, note="no")

    with pytest.raises(ValueError, match="already resolved"):
        drafts_mod.reject_submission(db, submission_id, note="again")
    with pytest.raises(ValueError, match="not found"):
        drafts_mod.accept_submission(
            db, "00000000-0000-0000-0000-000000000000", note=None
        )
