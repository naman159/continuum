from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_timeline_filters_by_cap_and_resolves_names(fake_db_factory, client):
    novel = make_novel()
    char_id = uuid4()
    chap1 = make_chapter(novel["id"], 1)
    chap5 = make_chapter(novel["id"], 5)
    fake_db_factory(
        novels=[novel],
        chapters=[chap1, chap5],
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
        events=[
            {
                "id": uuid4(),
                "chapter_id": chap1["id"],
                "description": "Alice arrives.",
                "event_type": "arrival",
                "impact_level": "medium",
                "involved_characters": [char_id],
                "involved_locations": [],
                "involved_objects": [],
                "created_at": datetime(2026, 1, 1),
            },
            {
                "id": uuid4(),
                "chapter_id": chap5["id"],
                "description": "Alice leaves.",
                "event_type": "action",
                "impact_level": "low",
                "involved_characters": [char_id],
                "involved_locations": [],
                "involved_objects": [],
                "created_at": datetime(2026, 1, 5),
            },
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/timeline?cap=3")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["description"] == "Alice arrives."
    assert rows[0]["involved_characters"] == ["Alice"]
