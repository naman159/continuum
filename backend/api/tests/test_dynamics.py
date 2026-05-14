from __future__ import annotations

from uuid import uuid4

from api.tests.conftest import make_chapter, make_novel


def test_list_shared_dynamics_returns_rows(fake_db_factory, client):
    novel = make_novel()
    a_id = uuid4()
    b_id = uuid4()
    chap1 = make_chapter(novel["id"], 1)
    dyn_id = uuid4()
    fake_db_factory(
        novels=[novel],
        chapters=[chap1],
        entities=[
            {"id": a_id, "novel_id": novel["id"], "entity_type": "character", "name": "Alice"},
            {"id": b_id, "novel_id": novel["id"], "entity_type": "character", "name": "Bob"},
        ],
        characters=[],
        shared_dynamics=[
            {
                "id": dyn_id,
                "entity_a_id": a_id,
                "entity_b_id": b_id,
                "chapter_id": chap1["id"],
                "description": "Alice and Bob are tense allies.",
            }
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/dynamics")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["description"] == "Alice and Bob are tense allies."
    assert body[0]["chapter_number"] == 1


def test_list_shared_dynamics_respects_cap(fake_db_factory, client):
    novel = make_novel()
    a_id = uuid4()
    b_id = uuid4()
    chap1 = make_chapter(novel["id"], 1)
    chap5 = make_chapter(novel["id"], 5)
    fake_db_factory(
        novels=[novel],
        chapters=[chap1, chap5],
        entities=[
            {"id": a_id, "novel_id": novel["id"], "entity_type": "character", "name": "Alice"},
            {"id": b_id, "novel_id": novel["id"], "entity_type": "character", "name": "Bob"},
        ],
        characters=[],
        shared_dynamics=[
            {"id": uuid4(), "entity_a_id": a_id, "entity_b_id": b_id, "chapter_id": chap1["id"], "description": "early"},
            {"id": uuid4(), "entity_a_id": a_id, "entity_b_id": b_id, "chapter_id": chap5["id"], "description": "late"},
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/dynamics?cap=3")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["description"] == "early"
