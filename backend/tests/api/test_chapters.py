from __future__ import annotations

from tests.api.conftest import make_chapter, make_novel


def test_list_chapters_filters_by_cap(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], n) for n in (1, 2, 3, 4, 5)],
    )
    response = client.get(f"/api/novels/{novel['id']}/chapters?cap=3")
    assert response.status_code == 200
    numbers = [r["number"] for r in response.json()]
    assert numbers == [1, 2, 3]
