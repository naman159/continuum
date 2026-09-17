"""`on_continuity_fail` as enforced by analyze_chapter.

One critique runs for every caller; the policy decides what a FAIL costs. These
pin both halves: that "block" actually refuses before extraction and parks the
draft, and that "warn" ingests the same failing draft while still recording the
findings — plus that nothing else (run_critic, replace, source) can turn a
blocking caller into a passing one.
"""

from __future__ import annotations

import pytest

from pipeline import pipeline as pipeline_mod
from pipeline.critic.service import DraftCritique
from pipeline.critic.types import CritiqueReport, Finding, Severity
from reads import drafts as drafts_reads

TEXT = "Elara walked into the hall."


def _fail_finding() -> Finding:
    return Finding(
        check="knowledge_state", severity=Severity.FAIL, message="nope",
        quote="She already knew.",
    )


def _failing() -> DraftCritique:
    report = CritiqueReport(novel_id="n", chapter_number=1, findings=[_fail_finding()])
    return DraftCritique(
        status="ok", report=report,
        fails=[{"check": "knowledge_state", "message": "nope", "quote": "She already knew."}],
    )


def _passing() -> DraftCritique:
    return DraftCritique(
        status="ok", report=CritiqueReport(novel_id="n", chapter_number=1)
    )


@pytest.fixture
def critique(monkeypatch):
    """Force a verdict, and count how often extraction ran."""
    calls = {"extract": 0, "critique": 0}

    def _install(verdict: DraftCritique):
        def _fake(db, **kw):
            calls["critique"] += 1
            return verdict

        monkeypatch.setattr(pipeline_mod, "critique_draft", _fake)

    # Extraction runs as ChapterExtractor(use_mock=…).extract_chapter(…) —
    # there is no module-level extract_chapter to patch. Subclass so the
    # ingesting paths still get real extraction behavior.
    original_cls = pipeline_mod.ChapterExtractor

    class _CountingExtractor(original_cls):  # type: ignore[misc,valid-type]
        def extract_chapter(self, *a, **kw):
            calls["extract"] += 1
            return super().extract_chapter(*a, **kw)

    monkeypatch.setattr(pipeline_mod, "ChapterExtractor", _CountingExtractor)
    _install.calls = calls  # type: ignore[attr-defined]
    return _install


def _chapter_count(db, novel_id: str, number: int) -> int:
    return int(
        db.fetchval(
            "SELECT COUNT(*) FROM chapters WHERE novel_id = %s AND number = %s",
            (novel_id, number),
        )
    )


def _analyze(db, novel_id, **kw):
    return pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text=TEXT, chapter_title="Ch 90",
        use_mock_llm=True, chunk_size=1000, chunk_overlap=100, db=db, **kw,
    )


# --------------------------------------------------------------------------
# block: refuse before writing
# --------------------------------------------------------------------------

def test_block_writes_no_chapter_and_never_extracts(db, seed_novel, critique):
    novel_id = seed_novel(db)["novel_id"]
    critique(_failing())

    result = _analyze(db, novel_id, on_continuity_fail="block")

    assert result["ingested"] is False
    assert result["status"] == "pending_review"
    assert result["reason"] == "fail"
    assert _chapter_count(db, novel_id, 90) == 0
    assert critique.calls["extract"] == 0


def test_block_parks_the_draft_with_its_findings(db, seed_novel, critique):
    novel_id = seed_novel(db)["novel_id"]
    critique(_failing())

    result = _analyze(db, novel_id, on_continuity_fail="block")
    parked = drafts_reads.get_submission(db, result["submission_id"])

    assert parked["status"] == "pending"
    assert parked["raw_text"] == TEXT
    assert parked["chapter_number"] == 90
    assert parked["findings"]["fails"][0]["quote"] == "She already knew."


def test_run_critic_false_cannot_disable_a_blocking_check(db, seed_novel, critique):
    """Otherwise run_critic=False is a bypass: no critique, no FAIL, no refusal."""
    novel_id = seed_novel(db)["novel_id"]
    critique(_failing())

    result = _analyze(db, novel_id, on_continuity_fail="block", run_critic=False)

    assert result["ingested"] is False
    assert _chapter_count(db, novel_id, 90) == 0
    assert critique.calls["critique"] == 1


def test_replace_true_does_not_relax_the_policy(db, seed_novel, critique):
    novel_id = seed_novel(db)["novel_id"]
    critique(_failing())

    result = _analyze(db, novel_id, on_continuity_fail="block", replace=True)

    assert result["ingested"] is False
    assert _chapter_count(db, novel_id, 90) == 0


def test_an_outage_refuses_without_parking(db, seed_novel, critique):
    """"unavailable" parks nothing: there is no verdict for a human to
    adjudicate, so reporting "pending_review" would claim a review that isn't
    happening. The caller must hold the text and retry, not revise."""
    novel_id = seed_novel(db)["novel_id"]
    critique(DraftCritique(status="unavailable", error="critic is disabled"))

    result = _analyze(db, novel_id, on_continuity_fail="block")

    assert result["ingested"] is False
    assert result["status"] == "refused"
    assert result["submission_id"] is None
    assert result["reason"] == "unavailable"
    assert drafts_reads.list_submissions(db, novel_id) == []


def test_a_critique_error_parks_rather_than_passing(db, seed_novel, critique):
    novel_id = seed_novel(db)["novel_id"]
    critique(DraftCritique(status="error", error="RuntimeError: boom"))

    result = _analyze(db, novel_id, on_continuity_fail="block")

    assert result["status"] == "pending_review"
    parked = drafts_reads.get_submission(db, result["submission_id"])
    assert "boom" in parked["findings"]["error"]


def test_a_passing_resubmission_clears_the_stale_pending_row(db, seed_novel, critique):
    """The core loop: submit, FAIL, park, revise, PASS. The v1 row must not sit
    at 'pending' forever — a reviewer opening it would hit the duplicate-chapter
    409 the moment they tried to accept."""
    novel_id = seed_novel(db)["novel_id"]

    critique(_failing())
    first = _analyze(db, novel_id, on_continuity_fail="block")
    assert first["status"] == "pending_review"

    critique(_passing())
    second = _analyze(db, novel_id, on_continuity_fail="block")

    assert "ingested" not in second
    assert _chapter_count(db, novel_id, 90) == 1
    superseded = drafts_reads.get_submission(db, first["submission_id"])
    assert superseded["status"] == "rejected"
    assert superseded["resolution_note"] == "superseded by a passing resubmission"
    assert drafts_reads.list_submissions(db, novel_id) == []


@pytest.mark.parametrize("failure_phase", ["extraction", "persistence", "supersede"])
def test_failed_resubmission_keeps_the_pending_draft(
    db, seed_novel, critique, monkeypatch, failure_phase,
):
    novel_id = seed_novel(db)["novel_id"]
    critique(_failing())
    first = _analyze(db, novel_id, on_continuity_fail="block")
    critique(_passing())

    def fail(*args, **kwargs):
        raise RuntimeError("replacement failed")

    if failure_phase == "extraction":
        monkeypatch.setattr(pipeline_mod.ChapterExtractor, "extract_chapter", fail)
    elif failure_phase == "persistence":
        monkeypatch.setattr(pipeline_mod, "persist_state_deltas", fail)
    else:
        supersede = pipeline_mod.supersede_pending

        def supersede_then_fail(*args, **kwargs):
            supersede(*args, **kwargs)
            fail()

        monkeypatch.setattr(pipeline_mod, "supersede_pending", supersede_then_fail)

    with pytest.raises(RuntimeError, match="replacement failed"):
        _analyze(db, novel_id, on_continuity_fail="block")

    assert _chapter_count(db, novel_id, 90) == 0
    parked = drafts_reads.get_submission(db, first["submission_id"])
    assert parked["status"] == "pending"
    assert parked["raw_text"] == TEXT


def test_resubmission_supersedes_the_previous_parked_row(db, seed_novel, critique):
    novel_id = seed_novel(db)["novel_id"]
    critique(_failing())

    first = _analyze(db, novel_id, on_continuity_fail="block")
    second = _analyze(db, novel_id, on_continuity_fail="block")

    superseded = drafts_reads.get_submission(db, first["submission_id"])
    assert superseded["status"] == "rejected"
    assert superseded["resolution_note"] == "superseded by resubmission"
    assert [r["id"] for r in drafts_reads.list_submissions(db, novel_id)] == [
        second["submission_id"]
    ]


# --------------------------------------------------------------------------
# warn: same verdict, different consequence
# --------------------------------------------------------------------------

def test_warn_is_the_default(db, seed_novel, critique):
    """A caller that says nothing gets the advisory policy — the gate is not
    something you have to remember to turn off."""
    novel_id = seed_novel(db)["novel_id"]
    critique(_failing())

    result = _analyze(db, novel_id)

    assert "ingested" not in result
    assert _chapter_count(db, novel_id, 90) == 1
    assert critique.calls["extract"] >= 1


def test_warn_ingests_a_failing_draft_but_records_the_findings(db, seed_novel, critique):
    novel_id = seed_novel(db)["novel_id"]
    critique(_failing())

    result = _analyze(db, novel_id, on_continuity_fail="warn")

    assert result["critique"]["passed"] is False
    assert result["critique"]["status"] == "ok"
    assert drafts_reads.list_submissions(db, novel_id) == []
    assert db.fetchval(
        "SELECT count(*) FROM critique_reports WHERE chapter_id = %s",
        (result["chapter_id"],),
    ) == 1


def test_warn_runs_the_critique_exactly_once(db, seed_novel, critique):
    """The whole point of collapsing the gate into phase 0: one claims
    extraction and one critique per chapter, not one to decide and another to
    record."""
    novel_id = seed_novel(db)["novel_id"]
    critique(_passing())

    _analyze(db, novel_id, on_continuity_fail="warn")

    assert critique.calls["critique"] == 1


def test_block_that_passes_also_runs_the_critique_once(db, seed_novel, critique):
    novel_id = seed_novel(db)["novel_id"]
    critique(_passing())

    result = _analyze(db, novel_id, on_continuity_fail="block")

    assert "ingested" not in result
    assert critique.calls["critique"] == 1
    assert db.fetchval(
        "SELECT count(*) FROM critique_reports WHERE chapter_id = %s",
        (result["chapter_id"],),
    ) == 1


def test_warn_with_run_critic_false_skips_the_critique_entirely(db, seed_novel, critique):
    novel_id = seed_novel(db)["novel_id"]
    critique(_failing())

    result = _analyze(db, novel_id, on_continuity_fail="warn", run_critic=False)

    assert result["critique"] is None
    assert critique.calls["critique"] == 0
    assert _chapter_count(db, novel_id, 90) == 1


def test_an_outage_under_warn_ingests_and_records_nothing(db, seed_novel, critique):
    novel_id = seed_novel(db)["novel_id"]
    critique(DraftCritique(status="unavailable", error="mock mode"))

    result = _analyze(db, novel_id, on_continuity_fail="warn")

    assert result["critique"]["status"] == "unavailable"
    assert result["critique"]["passed"] is None
    assert _chapter_count(db, novel_id, 90) == 1
    assert db.fetchval(
        "SELECT count(*) FROM critique_reports WHERE chapter_id = %s",
        (result["chapter_id"],),
    ) == 0


def test_failed_report_persistence_is_visible_to_the_caller(db, seed_novel, critique, monkeypatch):
    from pipeline import pipeline as pipeline_mod
    novel_id = seed_novel(db)["novel_id"]
    critique(_passing())

    def fail(*args, **kwargs):
        raise RuntimeError("database report write failed")

    monkeypatch.setattr(pipeline_mod, "persist_critique", fail)
    result = _analyze(db, novel_id, on_continuity_fail="warn")
    assert _chapter_count(db, novel_id, 90) == 1
    assert result["critique"]["persisted"] is False
