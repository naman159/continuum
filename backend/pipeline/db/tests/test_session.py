from __future__ import annotations

from contextlib import contextmanager

import pytest

from pipeline.db.client import DBClient, DBSession


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=None):
        self._conn.statements.append((query, params))

    def fetchone(self):
        return self._conn.next_row

    def fetchall(self):
        return list(self._conn.rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class FakeConn:
    def __init__(self):
        self.statements: list = []
        self.committed = 0
        self.rolled_back = 0
        self.next_row = ("value",)
        self.rows: list = []
        self.row_factories: list = []

    def cursor(self, row_factory=None):
        self.row_factories.append(row_factory)
        return FakeCursor(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def connection(self):
        yield self._conn


def _client_with(conn: FakeConn) -> DBClient:
    client = DBClient.__new__(DBClient)  # skip __init__ (no real pool)
    client._pool = FakePool(conn)
    return client


def test_session_shares_one_connection_and_commits_once():
    conn = FakeConn()
    client = _client_with(conn)
    with client.session() as s:
        s.execute("INSERT 1", (1,))
        s.execute("INSERT 2", (2,))
        assert s.fetchval("SELECT x") == "value"
    assert [q for q, _ in conn.statements] == ["INSERT 1", "INSERT 2", "SELECT x"]
    assert conn.committed == 1
    assert conn.rolled_back == 0


def test_session_rolls_back_on_exception():
    conn = FakeConn()
    client = _client_with(conn)
    with pytest.raises(RuntimeError):
        with client.session() as s:
            s.execute("INSERT 1", None)
            raise RuntimeError("boom")
    assert conn.committed == 0
    assert conn.rolled_back == 1


def test_session_accepts_and_ignores_commit_kwarg():
    """Existing persistence helpers pass commit=True; sessions must tolerate it."""
    conn = FakeConn()
    s = DBSession(conn)
    assert s.fetchval("SELECT 1", commit=True) == "value"
    s.fetchone("SELECT 1", dict_rows=False, commit=True)
    s.fetchall("SELECT 1", dict_rows=False, commit=True)
    assert conn.committed == 0  # session never commits on its own


def test_session_forwards_dict_row_factory_when_dict_rows_true():
    """Persistence helpers rely on dict_rows=True returning dict-like rows;
    the session must forward psycopg's dict_row factory to the cursor."""
    from psycopg.rows import dict_row

    conn = FakeConn()
    s = DBSession(conn)
    s.fetchone("SELECT 1", dict_rows=True)
    s.fetchall("SELECT 1", dict_rows=True)
    s.fetchone("SELECT 1", dict_rows=False)
    assert conn.row_factories == [dict_row, dict_row, None]
