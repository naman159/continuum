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


def test_build_character_page_not_found_suggests_close_names(monkeypatch):
    fake = FakeDB(
        fetchone_results=[None],
        fetchall_results=[[{"name": "Marla"}, {"name": "Jake"}]],
    )
    monkeypatch.setattr(queries, "DBClient", lambda: fake)
    with pytest.raises(ValueError) as exc:
        queries.build_character_page("novel-1", "Mara")
    assert "Marla" in str(exc.value)
    assert "closest names" in str(exc.value)


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
