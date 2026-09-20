from __future__ import annotations

from typing import Any


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

    import pipeline.critic.service as service_mod

    monkeypatch.setattr(service_mod, "extract_draft_claims", lambda text, use_mock=None: {})
    monkeypatch.setattr(service_mod, "build_draft_chapter", lambda db, **kw: object())
    monkeypatch.setattr(service_mod, "ContinuityCritic", StubCritic)

    # use_mock=False: mock mode is an outage, not a verdict (see critique_draft).
    out = queries.check_continuity("novel-1", 4, "draft text", db=FakeDB(), use_mock=False)
    assert out["passed"] is False
    assert out["status"] == "ok"
    assert out["fails"][0]["check"] == "knowledge_state"
    assert out["fails"][0]["quote"] == "the sword"
    assert out["warns"] == []


def test_check_continuity_reports_an_outage_as_status_not_a_pass(monkeypatch):
    """`passed` alone cannot distinguish "clean" from "never ran". An agent
    reading only `passed` on an unavailable critic would see False and try to
    revise; `status` is what tells it to retry instead."""
    out = queries.check_continuity("novel-1", 4, "draft text", db=FakeDB(), use_mock=True)
    assert out["passed"] is False
    assert out["status"] == "unavailable"
    assert out["fails"] == [] and out["warns"] == []


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
        "enrichment": None,
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


def test_save_chapter_passes_refusal_through(monkeypatch):
    """A gate refusal reaches the agent as ingested:False, not a raised error."""
    from mcp_server import queries as queries_mod

    refusal = {
        "ingested": False,
        "status": "pending_review",
        "submission_id": "11111111-1111-1111-1111-111111111111",
        "reason": "fail",
        "fails": [{"check": "knowledge_state", "message": "nope"}],
        "warns": [],
    }
    monkeypatch.setattr(queries_mod, "analyze_chapter", lambda **kw: refusal)

    result = queries_mod.save_chapter("n", 90, "draft text")

    assert result["ingested"] is False
    assert result["status"] == "pending_review"
    assert result["submission_id"] == "11111111-1111-1111-1111-111111111111"
    assert result["reason"] == "fail"
    assert result["fails"][0]["check"] == "knowledge_state"
    assert "chapter_id" not in result


def test_save_chapter_still_reports_success_on_ingest(monkeypatch):
    from mcp_server import queries as queries_mod

    monkeypatch.setattr(
        queries_mod, "analyze_chapter",
        lambda **kw: {"chapter_id": "abc", "materialized": True, "critique": {"passed": True}},
    )

    result = queries_mod.save_chapter("n", 90, "draft text")

    assert result["ingested"] is True
    assert result["chapter_id"] == "abc"
