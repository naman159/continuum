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


def test_list_custom_entities(fake_db_factory, client):
    novel = make_novel()
    entity_id = uuid4()
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        novel_entity_types=[
            {"id": uuid4(), "novel_id": novel["id"], "name": "realm", "description": "A dimension."},
        ],
        entities=[
            {"id": entity_id, "novel_id": novel["id"], "entity_type": "realm", "name": "The 93rd Universe"},
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/entity-types/realm/entities")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "The 93rd Universe"
    assert body[0]["entity_type"] == "realm"


def test_get_custom_entity_detail(fake_db_factory, client):
    novel = make_novel()
    entity_id = uuid4()
    other_entity_id = uuid4()
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        novel_entity_types=[
            {"id": uuid4(), "novel_id": novel["id"], "name": "realm", "description": "A dimension."},
        ],
        entities=[
            {"id": entity_id, "novel_id": novel["id"], "entity_type": "realm", "name": "The 93rd Universe"},
            {"id": other_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Jake"},
        ],
        relationships=[
            {
                "id": uuid4(),
                "entity_a_id": other_entity_id,
                "entity_b_id": entity_id,
                "rel_type": "inhabits",
                "from_chapter": 1,
                "to_chapter": None,
                "notes": None,
            }
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/custom-entities/{entity_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "The 93rd Universe"
    assert body["entity_type"] == "realm"
    assert len(body["relationships"]) == 1
    assert body["relationships"][0]["rel_type"] == "inhabits"


def test_get_custom_entity_detail_404(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[], entities=[])
    response = client.get(f"/api/novels/{novel['id']}/custom-entities/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 404
