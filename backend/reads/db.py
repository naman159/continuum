"""DB provider for the read layer. Tests may patch get_db."""

from __future__ import annotations

from pipeline.db.client import DBClient

_db: DBClient | None = None


def get_db() -> DBClient:
    global _db
    if _db is None:
        _db = DBClient()
    return _db
