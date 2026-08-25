"""Accepting and rejecting parked drafts."""

from __future__ import annotations

import json

import pytest

from pipeline import drafts as drafts_mod
from reads import drafts as drafts_reads


@pytest.fixture(autouse=True)
def _force_mock_llm(monkeypatch):
    """accept_submission hardcodes use_mock_llm=None (correct for production,
    where a real extraction should run). In tests that would make live LLM
    calls, so wrap analyze_chapter to force mock mode — everything else in
    the path stays real.
    """
    real = drafts_mod.analyze_chapter

    def _forced(**kwargs):
        kwargs["use_mock_llm"] = True
        return real(**kwargs)

    monkeypatch.setattr(drafts_mod, "analyze_chapter", _forced)


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
