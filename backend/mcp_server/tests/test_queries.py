from __future__ import annotations

from typing import Any

import pytest

from mcp_server import queries


class FakeDB:
    """Records every call; returns canned results in order (empty when exhausted)."""

    def __init__(self, fetchall_results=None, fetchone_results=None, fetchval_result=None):
        self.calls: list[tuple[str, str, Any]] = []
        self._fetchall = list(fetchall_results or [])
        self._fetchone = list(fetchone_results or [])
        self._fetchval = fetchval_result

    def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
        self.calls.append(("fetchall", query, params))
        return self._fetchall.pop(0) if self._fetchall else []

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        self.calls.append(("fetchone", query, params))
        return self._fetchone.pop(0) if self._fetchone else None

    def fetchval(self, query, params=None, *, commit=False):
        self.calls.append(("fetchval", query, params))
        return self._fetchval

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_check_continuity_serializes_critic_report(monkeypatch):
    from pipeline.critic.types import CritiqueReport, Finding, Severity

    report = CritiqueReport(novel_id="novel-1", chapter_number=4)
    report.findings.append(
        Finding(check="knowledge_state", severity=Severity.FAIL,
                message="Mara cannot know about the sword yet", quote="the sword")
    )

    class StubCritic:
        def __init__(self, db):
            pass

        def critique(self, draft):
            return report

    monkeypatch.setattr(queries, "extract_draft_claims", lambda text, use_mock=None: {})
    monkeypatch.setattr(queries, "build_draft_chapter", lambda db, **kw: object())
    monkeypatch.setattr(queries, "ContinuityCritic", StubCritic)

    out = queries.check_continuity("novel-1", 4, "draft text", db=FakeDB(), use_mock=True)
    assert out["passed"] is False
    assert out["fails"][0]["check"] == "knowledge_state"
    assert out["fails"][0]["quote"] == "the sword"
    assert out["warns"] == []


def test_save_chapter_reports_duplicate_as_error(monkeypatch):
    def boom(**kwargs):
        raise ValueError("chapter 3 already ingested for this novel")

    monkeypatch.setattr(queries, "analyze_chapter", boom)
    out = queries.save_chapter("novel-1", 3, "some prose", db=FakeDB())
    assert "error" in out
    assert "already ingested" in out["error"]


def test_save_chapter_success_and_flags(monkeypatch):
    seen = {}

    def fake_process(**kwargs):
        seen.update(kwargs)
        return {"chapter_id": "abc-123", "materialized": True, "critique": {"passed": True}}

    monkeypatch.setattr(queries, "analyze_chapter", fake_process)
    out = queries.save_chapter("novel-1", 9, "some prose", title="The Gate", db=FakeDB())
    assert out == {
        "ingested": True,
        "chapter_id": "abc-123",
        "materialized": True,
        "critique": {"passed": True},
    }
    assert seen["source"] == "agent"
    assert seen["replace"] is False
    assert seen["chapter_title"] == "The Gate"


def test_save_chapter_surfaces_best_effort_phase_failures(monkeypatch):
    """materialized/critique are the ONLY signal that the post-commit
    MATERIALIZE/CRITIQUE phases failed and a manual re-run is needed —
    the MCP tool must not swallow them into a bare success."""

    def fake_process(**kwargs):
        return {"chapter_id": "abc-123", "materialized": False, "critique": None}

    monkeypatch.setattr(queries, "analyze_chapter", fake_process)
    out = queries.save_chapter("novel-1", 9, "some prose", db=FakeDB())
    assert out["ingested"] is True
    assert out["materialized"] is False
    assert out["critique"] is None


def test_save_chapter_rejects_empty_text():
    out = queries.save_chapter("novel-1", 9, "   ")
    assert "error" in out
