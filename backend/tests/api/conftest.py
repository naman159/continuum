from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api import queries as queries_module


class FakeDB:
    """In-memory stand-in for db.client.DBClient for API tests."""

    def __init__(
        self,
        *,
        novels: list[dict[str, Any]] | None = None,
        chapters: list[dict[str, Any]] | None = None,
        characters: list[dict[str, Any]] | None = None,
        character_states: list[dict[str, Any]] | None = None,
        events: list[dict[str, Any]] | None = None,
        relationships: list[dict[str, Any]] | None = None,
        shared_dynamics: list[dict[str, Any]] | None = None,
        plot_threads: list[dict[str, Any]] | None = None,
        thread_events: list[dict[str, Any]] | None = None,
        continuity_flags: list[dict[str, Any]] | None = None,
        locations: list[dict[str, Any]] | None = None,
        objects: list[dict[str, Any]] | None = None,
        factions: list[dict[str, Any]] | None = None,
        entities: list[dict[str, Any]] | None = None,
    ) -> None:
        self.novels = novels or []
        self.chapters = chapters or []
        self.characters = characters or []
        self.character_states = character_states or []
        self.events = events or []
        self.relationships = relationships or []
        self.shared_dynamics = shared_dynamics or []
        self.plot_threads = plot_threads or []
        self.thread_events = thread_events or []
        self.continuity_flags = continuity_flags or []
        self.locations = locations or []
        self.objects = objects or []
        self.factions = factions or []
        self.entities = entities or []

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeDB":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"FakeDB has no attribute {name!r}. "
            f"If this looks like a DBClient method (e.g. fetchall/fetchval/execute), "
            f"the query function is missing its `hasattr(db, ...)` guard for the in-memory path."
        )


@pytest.fixture
def fake_db_factory(monkeypatch):
    """Return a factory that installs a FakeDB as the API's DB provider."""

    def _factory(**kwargs: Any) -> FakeDB:
        db = FakeDB(**kwargs)
        monkeypatch.setattr(queries_module, "_get_db", lambda: db)
        return db

    return _factory


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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


def make_chapter(novel_id: UUID, number: int, **overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "novel_id": novel_id,
        "number": number,
        "title": None,
        "summary": f"Summary of chapter {number}",
        "processed_at": datetime(2026, 1, 1) + timedelta(days=number - 1),
    }
    base.update(overrides)
    return base
