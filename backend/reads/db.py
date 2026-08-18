"""DB provider for the read layer. Tests may patch get_db."""

from __future__ import annotations

import threading

from pipeline.db.client import DBClient

_db: DBClient | None = None
_db_lock = threading.Lock()


def get_db() -> DBClient:
    """Process-wide read client.

    The lock matters: route handlers run on Starlette's threadpool, so an
    unsynchronized check-then-set lets two threads race on the first request
    and each construct a DBClient. DBClient opens its pool eagerly, so the
    loser's connections are leaked for the process lifetime with no reference
    left to close them.
    """
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = DBClient()
    return _db
