from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_relationships_graph_returns_nodes_and_edges(fake_db_factory, client):
    novel = make_novel()
    a = uuid4()
    b = uuid4()
    chap1 = make_chapter(novel["id"], 1)
    fake_db_factory(
        novels=[novel],
        chapters=[chap1],
        characters=[
            {"id": a, "novel_id": novel["id"], "name": "Alice", "aliases": [], "description": None, "first_appearance_chapter": 1},
            {"id": b, "novel_id": novel["id"], "name": "Bob", "aliases": [], "description": None, "first_appearance_chapter": 1},
        ],
        relationships=[
            {
                "id": uuid4(),
                "entity_a_id": a,
                "entity_a_type": "character",
                "entity_b_id": b,
                "entity_b_type": "character",
                "rel_type": "friend",
                "status": "active",
                "chapter_id": chap1["id"],
                "notes": None,
            }
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/relationships")
    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) == 2
    assert {n["label"] for n in body["nodes"]} == {"Alice", "Bob"}
    assert len(body["edges"]) == 1
    edge = body["edges"][0]
    assert edge["label"] == "friend"
