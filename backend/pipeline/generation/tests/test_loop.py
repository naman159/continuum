from __future__ import annotations

import uuid
from unittest.mock import patch

from pipeline.generation.loop import GeneratedChapter, generate_chapter
from pipeline.planner.types import ChapterPlan, ScenePlan


class LoopFakeDB:
    """Empty-result DB: style lookup and critic queries all return nothing.
    The plan itself is stubbed via the _mock_plan/gather_plan_context seam,
    so no planner context queries are exercised here."""

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        return None

    def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
        return []

    def fetchval(self, query, params=None, *, commit=False):
        return None

    def close(self):
        pass


def _fixed_plan() -> ChapterPlan:
    return ChapterPlan(
        novel_id="novel-1",
        chapter_number=7,
        title=None,
        arc_position="rising",
        chapter_goal="Advance the conflict by one beat.",
        scenes=[
            ScenePlan(
                scene_index=1, pov_character="Jake", location=None, time_anchor=None,
                present_characters=["Jake"], scene_goal="Jake reads the letter.",
            ),
        ],
    )


def _plan_seam():
    """Patch the mock-planning seam so the test controls the plan exactly."""
    return (
        patch("pipeline.generation.loop.gather_plan_context", lambda db, n, c: object()),
        patch("pipeline.generation.loop._mock_plan", lambda ctx: _fixed_plan()),
    )


def test_generate_chapter_mock_end_to_end_no_ingest():
    p1, p2 = _plan_seam()
    with p1, p2:
        result = generate_chapter(
            "novel-1", 7, db=LoopFakeDB(), ingest=False, use_mock=True, max_revisions=2
        )
    assert isinstance(result, GeneratedChapter)
    assert result.chapter_number == 7
    assert result.text.strip()                 # mock drafter produced prose
    assert result.report is not None
    assert result.report.passed                # empty claims -> no findings
    assert result.ingested is False


def test_generate_chapter_ingests_when_passing():
    captured: dict = {}

    def fake_process_chapter(**kwargs):
        captured.update(kwargs)
        return {"chapter_id": str(uuid.uuid4())}

    p1, p2 = _plan_seam()
    with p1, p2, patch("pipeline.generation.loop.process_chapter", fake_process_chapter):
        result = generate_chapter(
            "novel-1", 7, db=LoopFakeDB(), ingest=True, use_mock=True, max_revisions=2
        )

    assert result.ingested is True
    assert captured["source"] == "generated"
    assert captured["replace"] is False
    assert captured["generation_meta"]["chapter_goal"]


def test_generate_chapter_revises_then_gives_up_without_ingesting():
    """A critic that always fails forces the revision loop to its cap, and a
    failing report must block ingestion."""
    from pipeline.critic.types import CritiqueReport, Finding, Severity

    class AlwaysFailCritic:
        def __init__(self, db):
            pass

        def critique(self, draft):
            report = CritiqueReport(novel_id=draft.novel_id, chapter_number=draft.chapter_number)
            report.findings.append(
                Finding(check="entity_mention", severity=Severity.FAIL, message="nope")
            )
            return report

    ingest_called = {"n": 0}

    def fake_process_chapter(**kwargs):
        ingest_called["n"] += 1
        return {"chapter_id": "x"}

    p1, p2 = _plan_seam()
    with p1, p2, \
         patch("pipeline.generation.loop.ContinuityCritic", AlwaysFailCritic), \
         patch("pipeline.generation.loop.process_chapter", fake_process_chapter):
        result = generate_chapter(
            "novel-1", 7, db=LoopFakeDB(), ingest=True, use_mock=True, max_revisions=1
        )

    assert result.iterations == 2              # initial draft + 1 revision
    assert result.report.passed is False
    assert result.ingested is False
    assert ingest_called["n"] == 0
