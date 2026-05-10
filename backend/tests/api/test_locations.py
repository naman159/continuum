from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def _make_location(novel_id, name="Pemberley", **overrides):
    base = {
        "id": uuid4(),
        "novel_id": novel_id,
        "name": name,
        "aliases": [],
        "description": "A grand estate.",
        "first_appearance_chapter": 1,
    }
    base.update(overrides)
    return base


def test_list_locations(fake_db_factory, client):
    novel = make_novel()
    loc = _make_location(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        locations=[loc],
    )
    response = client.get(f"/api/novels/{novel['id']}/locations")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Pemberley"
    assert body[0]["first_appearance_chapter"] == 1


def test_list_locations_respects_cap(fake_db_factory, client):
    novel = make_novel()
    early = _make_location(novel["id"], name="Longbourn", first_appearance_chapter=1)
    late = _make_location(novel["id"], name="Pemberley", first_appearance_chapter=5)
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1), make_chapter(novel["id"], 5)],
        locations=[early, late],
    )
    response = client.get(f"/api/novels/{novel['id']}/locations?cap=3")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Longbourn"


def test_get_location_detail(fake_db_factory, client):
    novel = make_novel()
    loc = _make_location(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        locations=[loc],
    )
    response = client.get(f"/api/novels/{novel['id']}/locations/{loc['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == "Pemberley"
    assert body["events"] == []
    assert body["characters"] == []


def test_get_location_detail_404(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[make_chapter(novel["id"], 1)])
    response = client.get(f"/api/novels/{novel['id']}/locations/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
