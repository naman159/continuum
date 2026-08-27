from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import app
from pipeline.db.client import DBClient
from testing import seeding


class _NovelReapingClient(TestClient):
    """TestClient that records every novel created through POST /api/novels.

    The endpoint writes a real row, so a test that creates a novel and never
    deletes it leaks one permanently — 60 suite runs left 120 orphans behind
    before this existed. Recording happens here rather than in each test so
    new tests get the cleanup for free.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.created_novel_ids: list[str] = []

    def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> httpx.Response:
        response = super().request(method, url, *args, **kwargs)
        if method.upper() == "POST" and httpx.URL(url).path.rstrip("/") == "/api/novels":
            if response.status_code == 201:
                self.created_novel_ids.append(response.json()["id"])
        return response


@contextmanager
def novel_reaping_client() -> Iterator[_NovelReapingClient]:
    """API client whose exit cascade-deletes the novels it created."""
    api_client = _NovelReapingClient(app)
    try:
        yield api_client
    finally:
        if api_client.created_novel_ids:
            db = DBClient()
            try:
                for novel_id in api_client.created_novel_ids:
                    seeding.cleanup(db, novel_id)
            finally:
                db.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with novel_reaping_client() as api_client:
        yield api_client


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
