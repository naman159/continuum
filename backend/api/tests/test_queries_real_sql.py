"""Regression tests for the real-SQL branches of api.queries.

The FakeDB used by the route tests only exercises the in-memory branches, so
bugs in the SQL paths (which run against PostgreSQL in production) go unseen.
These tests stub fetchone/fetchall at the SQL level to pin down the contracts
that have broken before.
"""

from __future__ import annotations

from uuid import uuid4

from api import queries


class _SQLRecorder:
    """Minimal DBClient stand-in: no table attrs, records every query."""

    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.calls: list[tuple[str, tuple]] = []
        self._fetchone = fetchone_results or {}
        self._fetchall = fetchall_results or {}

    def _match(self, table: dict, query: str):
        for needle, result in table.items():
            if needle in query:
                return result
        return None

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        self.calls.append((query, tuple(params or ())))
        return self._match(self._fetchone, query)

    def fetchall(self, query, params=None, *, dict_rows=False, commit=False):
        self.calls.append((query, tuple(params or ())))
        return self._match(self._fetchall, query) or []

    def fetchval(self, query, params=None, *, commit=False):
        self.calls.append((query, tuple(params or ())))
        return 5  # max chapter


def test_faction_detail_queries_events_by_faction_id(monkeypatch):
    """events.involved_factions stores factions.id, NOT entities.id.

    A previous version looked up the faction's entities.id and queried
    involved_factions with it — every faction page showed zero events.
    """
    novel_id = uuid4()
    faction_id = uuid4()
    db = _SQLRecorder(
        fetchone_results={
            "FROM factions": {
                "id": faction_id,
                "name": "The Order",
                "aliases": [],
                "description": None,
            },
        },
    )
    monkeypatch.setattr(queries, "_get_db", lambda: db)

    detail = queries.get_faction_detail(novel_id, faction_id)
    assert detail is not None

    event_calls = [
        (q, p) for q, p in db.calls if "involved_factions)" in q and "ANY" in q
    ]
    assert event_calls, "faction detail must query events by involved_factions"
    _, params = event_calls[0]
    assert str(faction_id) in [str(p) for p in params], (
        "events must be filtered by the faction's typed-table id "
        f"(factions.id={faction_id}), got params {params}"
    )


def test_object_detail_relationship_sql_matches_either_direction(monkeypatch):
    """The relationships query must match the object as entity_a OR entity_b."""
    novel_id = uuid4()
    object_id = uuid4()
    db = _SQLRecorder(
        fetchone_results={
            "FROM objects": {
                "id": object_id,
                "name": "the One Ring",
                "aliases": [],
                "description": None,
                "significance": None,
                "first_appearance_chapter": 1,
                "entity_id": uuid4(),
            },
        },
    )
    monkeypatch.setattr(queries, "_get_db", lambda: db)

    detail = queries.get_object_detail(novel_id, object_id, cap=None)
    assert detail is not None

    rel_calls = [q for q, _ in db.calls if "FROM relationships" in q]
    assert rel_calls, "object detail must query relationships"
    sql = rel_calls[0]
    assert "entity_a_id = ob.entity_id" in sql and "entity_b_id = ob.entity_id" in sql, (
        "relationship lookup must be symmetric in the object's position"
    )
