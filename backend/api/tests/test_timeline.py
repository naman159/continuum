from __future__ import annotations

from uuid import uuid4

from api.tests.conftest import make_novel


def test_timeline_returns_entries_ordered_by_sort_order(fake_db_factory, client):
    novel = make_novel()
    char_id = uuid4()
    fake_db_factory(
        novels=[novel],
        characters=[
            {
                "id": char_id,
                "novel_id": novel["id"],
                "name": "Alice",
                "aliases": [],
                "description": None,
                "first_appearance_chapter": 1,
            }
        ],
        timeline=[
            {
                "id": uuid4(),
                "novel_id": novel["id"],
                "description": "Alice is born.",
                "story_date": "Year 1",
                "sort_order": 0,
                "involved_characters": [char_id],
                "involved_locations": [],
                "involved_objects": [],
                "involved_factions": [],
            },
            {
                "id": uuid4(),
                "novel_id": novel["id"],
                "description": "Alice trains as a swordsman.",
                "story_date": None,
                "sort_order": 10,
                "involved_characters": [char_id],
                "involved_locations": [],
                "involved_objects": [],
                "involved_factions": [],
            },
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/timeline")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    assert rows[0]["description"] == "Alice is born."
    assert rows[0]["story_date"] == "Year 1"
    assert rows[0]["involved_characters"] == ["Alice"]
    assert rows[1]["description"] == "Alice trains as a swordsman."
    assert rows[1]["story_date"] is None


def test_timeline_excludes_other_novels(fake_db_factory, client):
    novel = make_novel()
    other_novel = make_novel()
    fake_db_factory(
        novels=[novel, other_novel],
        timeline=[
            {
                "id": uuid4(),
                "novel_id": novel["id"],
                "description": "Event in novel.",
                "story_date": None,
                "sort_order": 0,
                "involved_characters": [],
                "involved_locations": [],
                "involved_objects": [],
                "involved_factions": [],
            },
            {
                "id": uuid4(),
                "novel_id": other_novel["id"],
                "description": "Event in other novel.",
                "story_date": None,
                "sort_order": 0,
                "involved_characters": [],
                "involved_locations": [],
                "involved_objects": [],
                "involved_factions": [],
            },
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/timeline")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["description"] == "Event in novel."
