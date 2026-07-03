from __future__ import annotations

from uuid import uuid4

from api import queries


class _Recorder:
    def __init__(self, fetchone_result=None):
        self.calls: list[tuple[str, tuple]] = []
        self._fetchone_result = fetchone_result

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        self.calls.append((query, tuple(params or ())))
        return self._fetchone_result

    def execute(self, query, params=None):
        self.calls.append((query, tuple(params or ())))

    def fetchval(self, query, params=None, *, commit=False):
        self.calls.append((query, tuple(params or ())))
        return 1


def test_patch_canon_fact_sets_lock(monkeypatch):
    db = _Recorder(fetchone_result={"id": uuid4()})
    monkeypatch.setattr(queries, "_get_db", lambda: db)
    ok = queries.update_canon_fact(uuid4(), uuid4(), locked=True, value=None)
    assert ok is True
    assert any("UPDATE canon_facts" in q and "locked" in q for q, _ in db.calls)


def test_patch_canon_fact_missing_returns_false(monkeypatch):
    db = _Recorder(fetchone_result=None)
    monkeypatch.setattr(queries, "_get_db", lambda: db)
    assert queries.update_canon_fact(uuid4(), uuid4(), locked=True, value=None) is False


def test_create_canon_fact_inserts(monkeypatch):
    db = _Recorder(fetchone_result={"id": uuid4()})
    monkeypatch.setattr(queries, "_get_db", lambda: db)
    row = queries.create_canon_fact(
        uuid4(),
        subject_entity_id=uuid4(),
        predicate="eye_color",
        value="green",
        kind="physical",
        locked=True,
    )
    assert row is not None
    assert any("INSERT INTO canon_facts" in q for q, _ in db.calls)


def test_delete_canon_fact(monkeypatch):
    db = _Recorder(fetchone_result={"id": uuid4()})
    monkeypatch.setattr(queries, "_get_db", lambda: db)
    assert queries.delete_canon_fact(uuid4(), uuid4()) is True
    assert any("DELETE FROM canon_facts" in q for q, _ in db.calls)
