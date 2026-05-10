from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def _make_faction(novel_id, name="The Order", **overrides):
    base = {
        "id": uuid4(),
        "novel_id": novel_id,
        "name": name,
        "aliases": [],
        "description": "A secret society.",
    }
    base.update(overrides)
    return base


def test_list_factions(fake_db_factory, client):
    novel = make_novel()
    faction = _make_faction(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        factions=[faction],
    )
    response = client.get(f"/api/novels/{novel['id']}/factions")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "The Order"


def test_get_faction_detail(fake_db_factory, client):
    novel = make_novel()
    faction = _make_faction(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        factions=[faction],
    )
    response = client.get(f"/api/novels/{novel['id']}/factions/{faction['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == "The Order"
    assert body["events"] == []
    assert body["characters"] == []


def test_get_faction_detail_404(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[make_chapter(novel["id"], 1)])
    response = client.get(f"/api/novels/{novel['id']}/factions/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
