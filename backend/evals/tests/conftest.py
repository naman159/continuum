"""DB fixtures for eval tests, mirroring reads/tests/conftest.py."""

from __future__ import annotations

import pytest

from pipeline.db.client import DBClient


@pytest.fixture(scope="module")
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="module")
def golden_novel(db):
    """Golden fixture novel ingested once per test module (mock LLM)."""
    from evals.harness import ingest_fixture

    novel_id = ingest_fixture(db, use_mock_llm=True)
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))
