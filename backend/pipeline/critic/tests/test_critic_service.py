"""The critic is optional, decoupled, and re-runnable.

Two properties this pins:

1. Which claims source a critique uses, and why mock must degrade to `reuse`
   (extract_draft_claims returns empty claims without a real LLM, and an empty
   draft passes the critic vacuously — worse than the cheaper mode).
2. CRITIC_ENABLED=false leaves ingestion otherwise untouched, so a chapter
   ingested with the critic off is still fully queryable and can be critiqued
   later via pipeline.critic.cli.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

import pipeline.critic.service as service
import pipeline.pipeline as pipeline_mod
from pipeline.config import settings


# --------------------------------------------------------------------------
# claims mode
# --------------------------------------------------------------------------

def _spy(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        service, "extract_draft_claims",
        lambda text, **kw: (calls.append("extract"), {"location_claims": []})[1],
    )
    monkeypatch.setattr(
        service, "build_draft_chapter",
        lambda db, **kw: (calls.append("build_chapter"), "DRAFT_EXTRACT")[1],
    )
    monkeypatch.setattr(
        service, "build_draft_from_extraction",
        lambda db, **kw: (calls.append("reuse"), "DRAFT_REUSE")[1],
    )
    return calls


def _build(monkeypatch, *, mode: str, use_mock):
    monkeypatch.setattr(service, "settings", replace(settings, critique_claims=mode))
    calls = _spy(monkeypatch)
    draft = service.build_draft(
        None, novel_id="n", chapter_number=1, raw_text="t",
        extracted={}, use_mock_llm=use_mock,
    )
    return draft, calls


def test_extract_mode_pulls_claims_from_chapter_text(monkeypatch):
    draft, calls = _build(monkeypatch, mode="extract", use_mock=False)
    assert draft == "DRAFT_EXTRACT"
    assert "extract" in calls and "reuse" not in calls


def test_mock_falls_back_to_reuse_even_in_extract_mode(monkeypatch):
    draft, calls = _build(monkeypatch, mode="extract", use_mock=True)
    assert draft == "DRAFT_REUSE", "mock must not produce an empty, vacuously-passing draft"
    assert "extract" not in calls


def test_reuse_mode_makes_no_extra_llm_call(monkeypatch):
    draft, calls = _build(monkeypatch, mode="reuse", use_mock=False)
    assert draft == "DRAFT_REUSE"
    assert "extract" not in calls


def test_extraction_failure_degrades_instead_of_losing_the_critique(monkeypatch):
    monkeypatch.setattr(service, "settings", replace(settings, critique_claims="extract"))
    calls = _spy(monkeypatch)

    def boom(text, **kw):
        calls.append("extract")
        raise RuntimeError("draft_claims: extraction failed")

    monkeypatch.setattr(service, "extract_draft_claims", boom)
    draft = service.build_draft(
        None, novel_id="n", chapter_number=1, raw_text="t",
        extracted={}, use_mock_llm=False,
    )
    assert draft == "DRAFT_REUSE"
    assert calls == ["extract", "reuse"]


# --------------------------------------------------------------------------
# on/off toggle + standalone re-run
# --------------------------------------------------------------------------

@pytest.fixture
def novel(db):
    novel_id = str(db.fetchval(
        "INSERT INTO novels (title) VALUES (%s) RETURNING id",
        (f"critic-toggle-{uuid.uuid4()}",), commit=True,
    ))
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def _reports(db, novel_id):
    return db.fetchval(
        """
        SELECT count(*) FROM critique_reports cr
        JOIN chapters ch ON ch.id = cr.chapter_id
        WHERE ch.novel_id = %s
        """,
        (novel_id,),
    )


def _ingest(novel_id, *, run_critic):
    return pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=1,
        raw_text="Aelric carried the silver dagger from Pellis Harbor to the Old Mill.",
        chapter_title="T", use_mock_llm=True, chunk_size=2000, chunk_overlap=200,
        run_critic=run_critic,
    )


def test_run_critic_false_skips_the_critique_but_still_ingests(db, novel):
    result = _ingest(novel, run_critic=False)

    assert result["critique"] is None
    assert _reports(db, novel) == 0
    # The memory layer itself is unaffected — this is the whole point of
    # decoupling: ingestion's job is the chapter, not the judgement.
    assert result["chapter_id"] is not None
    assert db.fetchval(
        "SELECT count(*) FROM chapters WHERE novel_id = %s", (novel,)
    ) == 1
    assert result["events"] >= 0


def test_a_skipped_chapter_can_be_critiqued_later(db, novel):
    _ingest(novel, run_critic=False)
    assert _reports(db, novel) == 0

    summary = service.critique_chapter(
        db, novel_id=novel, chapter_number=1, use_mock_llm=True
    )

    assert summary is not None
    assert _reports(db, novel) == 1


def test_re_critiquing_replaces_rather_than_duplicates(db, novel):
    _ingest(novel, run_critic=True)
    before = _reports(db, novel)

    service.critique_chapter(db, novel_id=novel, chapter_number=1, use_mock_llm=True)
    service.critique_chapter(db, novel_id=novel, chapter_number=1, use_mock_llm=True)

    assert _reports(db, novel) == before == 1


def test_critique_chapter_rejects_a_chapter_that_does_not_exist(db, novel):
    with pytest.raises(ValueError, match="does not exist"):
        service.critique_chapter(db, novel_id=novel, chapter_number=99, use_mock_llm=True)
