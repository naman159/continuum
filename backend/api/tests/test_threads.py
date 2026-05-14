from __future__ import annotations

from uuid import uuid4

from api.tests.conftest import make_chapter, make_novel


def test_threads_status_filter(fake_db_factory, client):
    novel = make_novel()
    open_thread = {
        "id": uuid4(),
        "novel_id": novel["id"],
        "title": "Open",
        "description": None,
        "status": "open",
        "thread_type": "goal",
        "opened_chapter": 1,
        "closed_chapter": None,
    }
    closed_thread = {
        "id": uuid4(),
        "novel_id": novel["id"],
        "title": "Closed",
        "description": None,
        "status": "closed",
        "thread_type": "mystery",
        "opened_chapter": 1,
        "closed_chapter": 2,
    }
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1), make_chapter(novel["id"], 2)],
        plot_threads=[open_thread, closed_thread],
    )
    response = client.get(f"/api/novels/{novel['id']}/threads?status=open")
    titles = [r["title"] for r in response.json()]
    assert titles == ["Open"]


def test_threads_closed_in_future_chapter_appears_open(fake_db_factory, client):
    novel = make_novel()
    thread = {
        "id": uuid4(),
        "novel_id": novel["id"],
        "title": "T",
        "description": None,
        "status": "closed",
        "thread_type": None,
        "opened_chapter": 1,
        "closed_chapter": 5,
    }
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], n) for n in (1, 5)],
        plot_threads=[thread],
    )
    response = client.get(f"/api/novels/{novel['id']}/threads?cap=3")
    rows = response.json()
    assert rows[0]["status"] == "progressing"
    assert rows[0]["closed_chapter"] is None
