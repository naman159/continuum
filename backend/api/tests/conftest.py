from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.app import app
from pipeline.db.client import DBClient
from reads.tests import seeding


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def real_db():
    """A real DBClient for endpoint tests migrated off FakeDB."""
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seed_novel_real(real_db: DBClient):
    """Function-returning fixture: call seed_novel_real(real_db) to seed a novel
    against real Postgres. Tracks every novel_id seeded during the test and
    deletes them (cascade) in the finalizer."""
    seeded_novel_ids: list[str] = []

    def _seed_novel(db_client: DBClient) -> dict[str, Any]:
        seeded = seeding.seed_novel(db_client)
        seeded_novel_ids.append(seeded["novel_id"])
        return seeded

    yield _seed_novel

    for novel_id in seeded_novel_ids:
        seeding.cleanup(real_db, novel_id)


def make_novel(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "title": "Test Novel",
        "author": "Author",
        "language": "en",
        "created_at": datetime(2026, 1, 1),
    }
    base.update(overrides)
    return base
