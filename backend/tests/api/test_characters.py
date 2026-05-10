from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_list_characters(fake_db_factory, client):
    novel = make_novel()
    char_id = uuid4()
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        characters=[
            {
                "id": char_id,
                "novel_id": novel["id"],
                "name": "Alice",
                "aliases": ["Allie"],
                "description": "Hero.",
                "first_appearance_chapter": 1,
            }
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/characters")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Alice"
    assert body[0]["aliases"] == ["Allie"]


def test_list_characters_respects_cap(fake_db_factory, client):
    """Characters whose first_appearance_chapter > cap are excluded."""
    novel = make_novel()
    early = uuid4()
    late = uuid4()
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1), make_chapter(novel["id"], 5)],
        characters=[
            {
                "id": early,
                "novel_id": novel["id"],
                "name": "Alice",
                "aliases": [],
                "description": None,
                "first_appearance_chapter": 1,
            },
            {
                "id": late,
                "novel_id": novel["id"],
                "name": "Bob",
                "aliases": [],
                "description": None,
                "first_appearance_chapter": 5,
            },
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/characters?cap=3")
    assert response.status_code == 200
    names = [row["name"] for row in response.json()]
    assert names == ["Alice"]


def test_character_detail_includes_states_within_cap(fake_db_factory, client):
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
                "description": "Hero.",
                "first_appearance_chapter": 1,
            }
        ],
        character_states=[
            {
                "id": uuid4(),
                "character_id": char_id,
                "chapter_id": chap1["id"],
                "location_id": None,
                "emotional_state": "calm",
                "goals": "explore",
                "knowledge": [],
                "physical_state": "ok",
                "notes": None,
                "created_at": datetime(2026, 1, 1),
            },
            {
                "id": uuid4(),
                "character_id": char_id,
                "chapter_id": chap5["id"],
                "location_id": None,
                "emotional_state": "tense",
                "goals": "survive",
                "knowledge": [],
                "physical_state": "wounded",
                "notes": None,
                "created_at": datetime(2026, 1, 5),
            },
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/characters/{char_id}?cap=3")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == "Alice"
    assert len(body["history"]) == 1
    assert body["history"][0]["chapter_number"] == 1
    assert body["current_state"]["chapter_number"] == 1
    assert body["events"] == []
    assert body["relationships"] == []


def test_character_detail_404(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[make_chapter(novel["id"], 1)])
    response = client.get(
        f"/api/novels/{novel['id']}/characters/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
