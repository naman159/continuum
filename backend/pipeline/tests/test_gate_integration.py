"""The gate as enforced by analyze_chapter — i.e. the bypass attempts."""

from __future__ import annotations

import pytest

from pipeline import pipeline as pipeline_mod
from pipeline.gate import GateVerdict


@pytest.fixture
def refusing_gate(monkeypatch):
    """Force the gate to refuse, and record whether extraction ever ran."""
    calls = {"extract": 0}

    def _fake_gate(db, **kw):
        return GateVerdict(
            passed=False, submission_id="11111111-1111-1111-1111-111111111111",
            fails=[{"check": "knowledge_state", "message": "nope"}], warns=[],
            reason="fail",
        )

    monkeypatch.setattr(pipeline_mod, "gate_agent_draft", _fake_gate)

    # Extraction runs as ChapterExtractor(use_mock=…).extract_chapter(…)
    # (pipeline.py:22 import, :307 call) — there is no module-level
    # extract_chapter to patch. Subclass so the human-source and bypass tests
    # still get real extraction behavior.
    original_cls = pipeline_mod.ChapterExtractor

    class _CountingExtractor(original_cls):  # type: ignore[misc,valid-type]
        def extract_chapter(self, *a, **kw):
            calls["extract"] += 1
            return super().extract_chapter(*a, **kw)

    monkeypatch.setattr(pipeline_mod, "ChapterExtractor", _CountingExtractor)
    return calls


@pytest.fixture
def passing_gate(monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "gate_agent_draft",
        lambda db, **kw: GateVerdict(passed=True, fails=[], warns=[]),
    )


def _chapter_count(db, novel_id: str, number: int) -> int:
    return int(
        db.fetchval(
            "SELECT COUNT(*) FROM chapters WHERE novel_id = %s AND number = %s",
            (novel_id, number),
        )
    )


def test_agent_fail_writes_no_chapter_row(db, seed_novel, refusing_gate):
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="bad draft",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent",
    )

    assert result["ingested"] is False
    assert result["status"] == "pending_review"
    assert result["reason"] == "fail"
    assert _chapter_count(db, novel_id, 90) == 0


def test_agent_fail_never_runs_extraction(db, seed_novel, refusing_gate):
    novel_id = seed_novel(db)["novel_id"]

    pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="bad draft",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent",
    )

    assert refusing_gate["extract"] == 0


def test_run_critic_false_does_not_bypass_the_gate(db, seed_novel, refusing_gate):
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="bad draft",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent", run_critic=False,
    )

    assert result["ingested"] is False
    assert _chapter_count(db, novel_id, 90) == 0


def test_replace_true_does_not_bypass_the_gate(db, seed_novel, refusing_gate):
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="bad draft",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent", replace=True,
    )

    assert result["ingested"] is False
    assert _chapter_count(db, novel_id, 90) == 0


def test_human_source_is_not_gated(db, seed_novel, refusing_gate):
    """A refusing gate must not affect human writes — it should never be called."""
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="Elara walked into the hall.",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="human",
    )

    assert "ingested" not in result
    assert _chapter_count(db, novel_id, 90) == 1
    # Positive control: this path DOES extract, so the counting patch must
    # have fired at least once — otherwise the zero-extraction assertion in
    # test_agent_fail_never_runs_extraction would pass vacuously even if the
    # patch stopped intercepting ChapterExtractor entirely.
    assert refusing_gate["extract"] >= 1


def test_gate_bypass_ingests_a_would_fail_draft(db, seed_novel, refusing_gate):
    """The human-override path used by accept_submission."""
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="Elara walked into the hall.",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent", _gate_bypass=True,
    )

    assert "ingested" not in result
    assert _chapter_count(db, novel_id, 90) == 1


def test_agent_pass_ingests_normally(db, seed_novel, passing_gate):
    novel_id = seed_novel(db)["novel_id"]

    result = pipeline_mod.analyze_chapter(
        novel_id=novel_id, chapter_number=90, raw_text="Elara walked into the hall.",
        chapter_title=None, use_mock_llm=True, chunk_size=1000,
        chunk_overlap=100, db=db, source="agent",
    )

    assert "ingested" not in result
    assert _chapter_count(db, novel_id, 90) == 1
