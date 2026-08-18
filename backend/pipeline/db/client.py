from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Sequence

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from pipeline.config import settings


class DBClient:
    def __init__(
        self, dsn: str | None = None, minconn: int = 1, maxconn: int = 5
    ) -> None:
        self._pool = ConnectionPool(
            conninfo=dsn or settings.database_url,
            min_size=minconn,
            max_size=maxconn,
            open=True,
        )

    def __enter__(self) -> DBClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def connection(self):
        with self._pool.connection() as conn:
            yield conn

    @contextmanager
    def cursor(self, commit: bool = False, dict_rows: bool = False):
        with self.connection() as conn:
            cur = conn.cursor(row_factory=dict_row) if dict_rows else conn.cursor()
            try:
                yield cur
                if commit:
                    conn.commit()
                else:
                    conn.rollback()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

    @contextmanager
    def transaction(self):
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

    @contextmanager
    def session(self):
        """Yield a DBSession bound to one connection; commit on success,
        roll back on exception."""
        with self._pool.connection() as conn:
            try:
                yield DBSession(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def execute(self, query: str, params: Sequence[Any] | None = None) -> None:
        with self.cursor(commit=True) as cur:
            cur.execute(query, params)

    def fetchone(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *,
        dict_rows: bool = False,
        commit: bool = False,
    ) -> Any:
        with self.cursor(commit=commit, dict_rows=dict_rows) as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def fetchall(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *,
        dict_rows: bool = False,
        commit: bool = False,
    ) -> list[Any]:
        with self.cursor(commit=commit, dict_rows=dict_rows) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            return list(rows)

    def fetchval(
        self, query: str, params: Sequence[Any] | None = None, *, commit: bool = False
    ) -> Any:
        row = self.fetchone(query, params, commit=commit)
        if row is None:
            return None
        return row[0]

    def execute_many(self, query: str, rows: Iterable[Sequence[Any]]) -> None:
        with self.cursor(commit=True) as cur:
            for row in rows:
                cur.execute(query, row)

    def close(self) -> None:
        self._pool.close()


class DBSession:
    """Single-connection view of the DBClient query API.

    Every statement issued through a session runs on one pooled connection and
    therefore inside one transaction; commit/rollback is owned by
    ``DBClient.session()``. The ``commit`` kwargs accepted by DBClient methods
    are accepted here and ignored so existing persistence helpers work
    unchanged when handed a session instead of a client.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    @contextmanager
    def savepoint(self):
        """Isolate a block of statements behind a SAVEPOINT.

        Postgres aborts the whole transaction on any statement error, so a
        caller that catches an exception and keeps issuing statements on the
        same connection gets ``InFailedSqlTransaction`` for everything that
        follows — and the eventual COMMIT is silently converted to ROLLBACK.
        Wrapping a best-effort block here rolls back just that block, leaving
        the surrounding chapter transaction usable.
        """
        with self._conn.transaction():
            yield

    def execute(self, query: str, params: Sequence[Any] | None = None) -> None:
        with self._conn.cursor() as cur:
            cur.execute(query, params)

    def fetchone(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *,
        dict_rows: bool = False,
        commit: bool = False,
    ) -> Any:
        factory = dict_row if dict_rows else None
        with self._conn.cursor(row_factory=factory) as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def fetchall(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *,
        dict_rows: bool = False,
        commit: bool = False,
    ) -> list[Any]:
        factory = dict_row if dict_rows else None
        with self._conn.cursor(row_factory=factory) as cur:
            cur.execute(query, params)
            return list(cur.fetchall())

    def fetchval(
        self, query: str, params: Sequence[Any] | None = None, *, commit: bool = False
    ) -> Any:
        row = self.fetchone(query, params)
        if row is None:
            return None
        return row[0]


__all__ = ["DBClient", "DBSession"]
