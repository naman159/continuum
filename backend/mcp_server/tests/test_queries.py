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


def test_list_open_threads_applies_cutoff_to_threads_and_events():
    thread_row = {
        "id": "t-1", "title": "The letter", "description": None, "status": "open",
        "thread_type": "mystery", "opened_chapter": 2, "closed_chapter": None,
    }
    fake = FakeDB(fetchall_results=[[thread_row], []])
    result = queries.list_open_threads("novel-1", 5, db=fake)

    assert result[0]["title"] == "The letter"
    thread_call = fake.calls[0]
    assert thread_call[2] == ("novel-1", 5, 5)          # opened<=5 AND (closed IS NULL OR closed>5)
    events_call = fake.calls[1]
    assert events_call[2] == ("t-1", 5)                  # events capped at chapter 5


def test_list_open_threads_owned_db_is_closed(monkeypatch):
    closed = []
    fake = FakeDB(fetchall_results=[[]])
    fake.close = lambda: closed.append(True)
    monkeypatch.setattr(queries, "DBClient", lambda: fake)
    queries.list_open_threads("novel-1", 3)
    assert closed == [True]


class FakeRetriever:
    def __init__(self, results=None):
        self.seen_query = None
        self._results = results or []

    def retrieve(self, query, use_rerank=False):
        from pipeline.retrieval.types import RetrievalBundle

        self.seen_query = query
        return RetrievalBundle(query=query, results=self._results)


def test_search_story_caps_max_chapter_and_serializes():
    from pipeline.retrieval.types import RetrievalResult

    retriever = FakeRetriever(
        results=[
            RetrievalResult(
                item_id="i1", kind="chunk", score=0.9,
                snippet="Jake read the letter.", chapter_number=3,
            )
        ]
    )
    out = queries.search_story("novel-1", "the letter", 12, retriever=retriever)
    assert retriever.seen_query.max_chapter == 11
    assert retriever.seen_query.novel_id == "novel-1"
    assert out["results"][0]["snippet"] == "Jake read the letter."
    assert out["results"][0]["chapter_number"] == 3


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
        return {"chapter_id": "abc-123"}

    monkeypatch.setattr(queries, "analyze_chapter", fake_process)
    out = queries.save_chapter("novel-1", 9, "some prose", title="The Gate", db=FakeDB())
    assert out == {"ingested": True, "chapter_id": "abc-123"}
    assert seen["source"] == "agent"
    assert seen["replace"] is False
    assert seen["chapter_title"] == "The Gate"


def test_save_chapter_rejects_empty_text():
    out = queries.save_chapter("novel-1", 9, "   ")
    assert "error" in out
