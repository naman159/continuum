from __future__ import annotations

from uuid import uuid4

from api.tests.conftest import make_novel


def _make_chapter(novel_id, number):
    return {"id": uuid4(), "novel_id": novel_id, "number": number, "title": None, "summary": None, "processed_at": None}


def _make_event(chapter_id, description, char_ids=None):
    return {
        "id": uuid4(),
        "chapter_id": chapter_id,
        "description": description,
        "event_type": "action",
        "impact_level": "medium",
        "involved_characters": char_ids or [],
        "involved_locations": [],
        "involved_objects": [],
        "involved_factions": [],
    }


def test_timeline_returns_events_ordered_by_chapter(fake_db_factory, client):
    novel = make_novel()
    char_id = uuid4()
    ch1 = _make_chapter(novel["id"], 1)
    ch2 = _make_chapter(novel["id"], 2)
    fake_db_factory(
        novels=[novel],
        chapters=[ch1, ch2],
        characters=[{"id": char_id, "novel_id": novel["id"], "name": "Alice", "aliases": [], "description": None, "first_appearance_chapter": 1}],
        events=[
            _make_event(ch2["id"], "Alice trains.", [char_id]),
            _make_event(ch1["id"], "Alice is born.", [char_id]),
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/timeline")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    assert rows[0]["chapter_number"] == 1
    assert rows[0]["description"] == "Alice is born."
    assert rows[0]["involved_characters"] == ["Alice"]
    assert rows[1]["chapter_number"] == 2


def test_timeline_cap_filters_chapters(fake_db_factory, client):
    novel = make_novel()
    ch1 = _make_chapter(novel["id"], 1)
    ch2 = _make_chapter(novel["id"], 2)
    fake_db_factory(
        novels=[novel],
        chapters=[ch1, ch2],
        events=[
            _make_event(ch1["id"], "Chapter 1 event."),
            _make_event(ch2["id"], "Chapter 2 event."),
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/timeline?cap=1")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["description"] == "Chapter 1 event."


def test_timeline_excludes_other_novels(fake_db_factory, client):
    novel = make_novel()
    other_novel = make_novel()
    ch1 = _make_chapter(novel["id"], 1)
    ch2 = _make_chapter(other_novel["id"], 1)
    fake_db_factory(
        novels=[novel, other_novel],
        chapters=[ch1, ch2],
        events=[
            _make_event(ch1["id"], "Event in novel."),
            _make_event(ch2["id"], "Event in other novel."),
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/timeline")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["description"] == "Event in novel."
