"""Postgres fixtures for pipeline tests (mirrors reads/tests/conftest.py)."""

from __future__ import annotations

from typing import Any

import pytest

from pipeline.db.client import DBClient
from reads.tests import seeding


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seed_novel(db: DBClient):
    seeded_novel_ids: list[str] = []

    def _seed_novel(db_client: DBClient) -> dict[str, Any]:
        seeded = seeding.seed_novel(db_client)
        seeded_novel_ids.append(seeded["novel_id"])
        return seeded

    yield _seed_novel

    for novel_id in seeded_novel_ids:
        seeding.cleanup(db, novel_id)
