"""Root fixtures shared by every backend suite.

``db`` and ``seed_novel`` were previously copy-pasted into four conftest files
and fourteen test modules; pytest resolves them from here for anything under
``backend/``. Suites that need a different lifetime (``evals/tests``,
``pipeline/retrieval/tests``, and the integration tests that seed once per
module) still declare their own module-scoped ``db`` — a nearer fixture wins,
and that scope difference is deliberate, not drift.
"""

from __future__ import annotations

from typing import Any

import pytest

from pipeline.db.client import DBClient
from testing import seeding


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seed_novel(db: DBClient):
    """Function-returning fixture: call ``seed_novel(db)`` to seed a novel.

    Tracks every novel_id seeded during the test and deletes them (cascade)
    in the finalizer, so callers don't have to clean up themselves.
    """
    seeded_novel_ids: list[str] = []

    def _seed_novel(db_client: DBClient) -> dict[str, Any]:
        seeded = seeding.seed_novel(db_client)
        seeded_novel_ids.append(seeded["novel_id"])
        return seeded

    yield _seed_novel

    for novel_id in seeded_novel_ids:
        seeding.cleanup(db, novel_id)
