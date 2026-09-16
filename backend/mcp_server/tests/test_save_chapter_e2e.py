"""End-to-end coverage for save_chapter through the real refusal stack.

Every other test in this package monkeypatches `analyze_chapter` (see
test_queries.py), which means none of them exercise the actual wiring
between `mcp_server.queries.save_chapter`, `pipeline.analyze_chapter`, and
`pipeline.critic.service.critique_draft`. This is the test that proves
save_chapter still asks for `on_continuity_fail="block"` — drop that one
argument and every other test in the suite still passes while agent writes
sail into canon.

This uses a real DBClient against the branch Postgres, the real save_chapter,
the real analyze_chapter, and the real critique_draft. The only thing stubbed
is the ContinuityCritic's verdict, so the test is deterministic and never
makes a real LLM call: save_chapter hardcodes `use_mock_llm=None` and has no
parameter letting a caller opt into mock mode, so this is the only seam
available for that.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mcp_server import queries as queries_mod
import pipeline.critic.service as service_mod
from pipeline.critic.types import CritiqueReport, Finding, Severity
from reads import drafts as drafts_reads


def _failing_report() -> CritiqueReport:
    report = CritiqueReport(novel_id="n", chapter_number=90)
    report.findings.append(
        Finding(
            check="knowledge_state",
            severity=Severity.FAIL,
            message="Elara knows something she shouldn't",
            quote="She already knew.",
        )
    )
    return report


@pytest.fixture
def stub_critic_verdict(monkeypatch):
    """Force ContinuityCritic to return a deterministic FAIL. Does not touch
    analyze_chapter, critique_draft, or save_chapter."""
    monkeypatch.setattr(
        service_mod, "settings",
        replace(service_mod.settings, use_mock_llm=False, critic_enabled=True),
    )
    monkeypatch.setattr(
        service_mod, "extract_draft_claims", lambda text, use_mock=None: {"mentions": []}
    )
    monkeypatch.setattr(service_mod, "build_draft_chapter", lambda db, **kw: object())

    class _FailingCritic:
        def __init__(self, db):
            pass

        def critique(self, draft):
            return _failing_report()

    monkeypatch.setattr(service_mod, "ContinuityCritic", _FailingCritic)


def test_save_chapter_refuses_through_the_real_stack(db, seed_novel, stub_critic_verdict):
    """A FAIL critic verdict must reach save_chapter's caller as
    ingested:False, with no chapter row written and the draft parked for
    human review — proven through the real save_chapter -> analyze_chapter
    -> critique_draft wiring, not a monkeypatched analyze_chapter."""
    novel_id = seed_novel(db)["novel_id"]

    result = queries_mod.save_chapter(novel_id, 90, "a bad draft", db=db)

    assert result["ingested"] is False
    assert result["status"] == "pending_review"
    assert result["reason"] == "fail"
    assert result["submission_id"] is not None
    assert result["fails"][0]["check"] == "knowledge_state"

    chapter_count = db.fetchval(
        "SELECT COUNT(*) FROM chapters WHERE novel_id = %s AND number = %s",
        (novel_id, 90),
    )
    assert int(chapter_count) == 0

    parked = drafts_reads.get_submission(db, result["submission_id"])
    assert parked is not None
    assert parked["status"] == "pending"
    assert parked["novel_id"] == novel_id
    assert parked["chapter_number"] == 90
