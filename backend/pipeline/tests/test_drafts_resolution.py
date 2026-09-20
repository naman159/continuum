"""Accepting and rejecting parked drafts."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from pipeline import drafts as drafts_mod
from reads import drafts as drafts_reads
from testing.drafts import _force_mock_llm  # noqa: F401  (autouse fixture)


def _park(db, novel_id: str, number: int = 4, text: str = "Elara walked in.") -> str:
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
            VALUES (%s, %s, 'Ch 4', %s, 'pending', %s::jsonb)
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
            "SELECT COUNT(*) FROM chapters WHERE novel_id = %s AND number = 4",
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


@pytest.mark.parametrize("failure", ["status", "flags"])
def test_accept_rolls_back_chapter_and_review_together(db, seed_novel, monkeypatch, failure):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)
    real_session = db.session

    @contextmanager
    def failing_session():
        with real_session() as session:
            fetchval = session.fetchval
            execute = session.execute

            def fail_status(query, params=None, **kwargs):
                if failure == "status" and "UPDATE draft_submissions" in query:
                    raise RuntimeError("status write failed")
                return fetchval(query, params, **kwargs)

            def fail_flags(query, params=None):
                if failure == "flags" and "INSERT INTO continuity_flags" in query:
                    raise RuntimeError("flags write failed")
                return execute(query, params)

            session.fetchval = fail_status
            session.execute = fail_flags
            yield session

    monkeypatch.setattr(db, "session", failing_session)
    with pytest.raises(RuntimeError, match="write failed"):
        drafts_mod.accept_submission(db, submission_id, note="deliberate retcon")
    assert db.fetchval(
        "SELECT id FROM chapters WHERE novel_id = %s AND number = 4", (novel_id,)
    ) is None
    assert drafts_reads.get_submission(db, submission_id)["status"] == "pending"

    # The failure is recoverable: retry the same draft after the DB recovers.
    monkeypatch.setattr(db, "session", real_session)
    assert drafts_mod.accept_submission(db, submission_id)["accepted"] is True


def test_accept_rejects_empty_edited_text(db, seed_novel):
    novel_id = seed_novel(db)["novel_id"]
    submission_id = _park(db, novel_id)
    with pytest.raises(ValueError, match="must not be empty"):
        drafts_mod.accept_submission(db, submission_id, edited_text="  ")
    assert drafts_reads.get_submission(db, submission_id)["status"] == "pending"


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
            "SELECT COUNT(*) FROM chapters WHERE novel_id = %s AND number = 4",
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
    """A stale acceptance must neither overwrite a rejection nor ingest its chapter."""
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
    assert db.fetchval("SELECT id FROM chapters WHERE novel_id = %s AND number = 4", (novel_id,)) is None


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
