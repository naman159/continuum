from __future__ import annotations

from api.tests.conftest import novel_reaping_client
from pipeline.db.client import DBClient


def test_reaping_client_deletes_novels_created_through_the_api(real_db: DBClient):
    """The API test client must not leak novels.

    POST /api/novels writes a real row; without a teardown that removes it,
    every test run permanently adds a novel to the database.
    """
    with novel_reaping_client() as api_client:
        response = api_client.post("/api/novels", json={"title": "Teardown Probe"})
        assert response.status_code == 201
        novel_id = response.json()["id"]
        assert real_db.fetchone("SELECT id FROM novels WHERE id = %s", (novel_id,)) is not None

    assert real_db.fetchone("SELECT id FROM novels WHERE id = %s", (novel_id,)) is None
