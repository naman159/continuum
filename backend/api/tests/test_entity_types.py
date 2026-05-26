from __future__ import annotations

from uuid import uuid4

from api.tests.conftest import make_novel, make_chapter


def test_list_genres(fake_db_factory, client):
    fake_db_factory()
    response = client.get("/api/genres")
    assert response.status_code == 200
    body = response.json()
    ids = {g["id"] for g in body}
    assert "litrpg" in ids
    assert "high_fantasy" in ids


def test_create_novel_with_entity_types(fake_db_factory, client):
    fake_db_factory()
    response = client.post("/api/novels", json={
        "title": "My LitRPG",
        "custom_entity_types": [
            {"name": "realm", "description": "A distinct universe."},
            {"name": "power_system", "description": "Named ability system."},
        ],
    })
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "My LitRPG"


def test_create_novel_no_custom_types(fake_db_factory, client):
    fake_db_factory()
    response = client.post("/api/novels", json={"title": "Plain Novel"})
    assert response.status_code == 201


def test_list_entity_types(fake_db_factory, client):
    novel = make_novel()
    db = fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        novel_entity_types=[
            {"id": uuid4(), "novel_id": novel["id"], "name": "realm", "description": "A dimension."},
            {"id": uuid4(), "novel_id": novel["id"], "name": "power_system", "description": "Ability system."},
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/entity-types")
    assert response.status_code == 200
    body = response.json()
    names = {t["name"] for t in body}
    assert names == {"realm", "power_system"}
