from __future__ import annotations

from tests.api.conftest import make_chapter, make_novel


def test_list_novels_returns_max_chapter(fake_db_factory, client):
    novel = make_novel(title="My Novel")
    fake_db_factory(
        novels=[novel],
        chapters=[
            make_chapter(novel["id"], 1),
            make_chapter(novel["id"], 2),
            make_chapter(novel["id"], 3),
        ],
    )
    response = client.get("/api/novels")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["title"] == "My Novel"
    assert body[0]["max_chapter"] == 3


def test_list_novels_empty(fake_db_factory, client):
    fake_db_factory()
    response = client.get("/api/novels")
    assert response.status_code == 200
    assert response.json() == []


def test_get_novel(fake_db_factory, client):
    novel = make_novel(title="My Novel", author="Me")
    fake_db_factory(novels=[novel], chapters=[make_chapter(novel["id"], 1)])
    response = client.get(f"/api/novels/{novel['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "My Novel"
    assert body["author"] == "Me"
    assert body["max_chapter"] == 1


def test_get_novel_404(fake_db_factory, client):
    fake_db_factory()
    response = client.get("/api/novels/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
