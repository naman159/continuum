from __future__ import annotations

from uuid import uuid4

from api.tests.conftest import make_chapter, make_novel


def test_entity_graph_includes_all_entity_types(fake_db_factory, client):
    novel = make_novel()
    chap1 = make_chapter(novel["id"], 1)

    char_entity_id = uuid4()
    char_id = uuid4()
    loc_entity_id = uuid4()
    loc_id = uuid4()
    obj_entity_id = uuid4()
    obj_id = uuid4()
    fac_entity_id = uuid4()
    fac_id = uuid4()
    custom_entity_id = uuid4()

    fake_db_factory(
        novels=[novel],
        chapters=[chap1],
        entities=[
            {"id": char_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Aria"},
            {"id": loc_entity_id,  "novel_id": novel["id"], "entity_type": "location",  "name": "The Castle"},
            {"id": obj_entity_id,  "novel_id": novel["id"], "entity_type": "object",    "name": "Magic Sword"},
            {"id": fac_entity_id,  "novel_id": novel["id"], "entity_type": "faction",   "name": "The Guild"},
            {"id": custom_entity_id, "novel_id": novel["id"], "entity_type": "spell",   "name": "Fireball"},
        ],
        characters=[
            {"id": char_id, "entity_id": char_entity_id, "novel_id": novel["id"],
             "name": "Aria", "aliases": [], "description": None, "first_appearance_chapter": 1},
        ],
        locations=[
            {"id": loc_id, "entity_id": loc_entity_id, "novel_id": novel["id"], "name": "The Castle"},
        ],
        objects=[
            {"id": obj_id, "entity_id": obj_entity_id, "novel_id": novel["id"], "name": "Magic Sword"},
        ],
        factions=[
            {"id": fac_id, "entity_id": fac_entity_id, "novel_id": novel["id"], "name": "The Guild"},
        ],
        relationships=[
            {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": loc_entity_id,
             "rel_type": "lives in", "from_chapter": 1, "to_chapter": None, "notes": None},
            {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": obj_entity_id,
             "rel_type": "wields", "from_chapter": 1, "to_chapter": None, "notes": None},
            {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": fac_entity_id,
             "rel_type": "member of", "from_chapter": 1, "to_chapter": None, "notes": None},
            {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": custom_entity_id,
             "rel_type": "knows", "from_chapter": 1, "to_chapter": None, "notes": None},
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/entity-graph")
    assert response.status_code == 200
    body = response.json()
    labels = {n["label"] for n in body["nodes"]}
    assert labels == {"Aria", "The Castle", "Magic Sword", "The Guild", "Fireball"}
    types = {n["entity_type"] for n in body["nodes"]}
    assert types == {"character", "location", "object", "faction", "spell"}
    assert len(body["edges"]) == 4


def test_entity_graph_native_ids(fake_db_factory, client):
    """native_id is the table-specific row ID, not the entity ID."""
    novel = make_novel()
    chap1 = make_chapter(novel["id"], 1)

    char_entity_id = uuid4()
    char_id = uuid4()
    loc_entity_id = uuid4()
    loc_id = uuid4()
    custom_entity_id = uuid4()

    fake_db_factory(
        novels=[novel],
        chapters=[chap1],
        entities=[
            {"id": char_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Aria"},
            {"id": loc_entity_id,  "novel_id": novel["id"], "entity_type": "location",  "name": "The Castle"},
            {"id": custom_entity_id, "novel_id": novel["id"], "entity_type": "spell",   "name": "Fireball"},
        ],
        characters=[
            {"id": char_id, "entity_id": char_entity_id, "novel_id": novel["id"],
             "name": "Aria", "aliases": [], "description": None, "first_appearance_chapter": 1},
        ],
        locations=[
            {"id": loc_id, "entity_id": loc_entity_id, "novel_id": novel["id"], "name": "The Castle"},
        ],
        relationships=[
            {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": loc_entity_id,
             "rel_type": "at", "from_chapter": 1, "to_chapter": None, "notes": None},
            {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": custom_entity_id,
             "rel_type": "knows", "from_chapter": 1, "to_chapter": None, "notes": None},
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/entity-graph")
    assert response.status_code == 200
    nodes = {n["entity_type"]: n for n in response.json()["nodes"]}

    assert nodes["character"]["native_id"] == str(char_id)
    assert nodes["location"]["native_id"] == str(loc_id)
    assert nodes["spell"]["native_id"] == nodes["spell"]["id"]


def test_entity_graph_cap_filters_characters(fake_db_factory, client):
    novel = make_novel()
    chap1 = make_chapter(novel["id"], 1)
    chap3 = make_chapter(novel["id"], 3)

    early_entity_id = uuid4()
    early_char_id = uuid4()
    late_entity_id = uuid4()
    late_char_id = uuid4()

    fake_db_factory(
        novels=[novel],
        chapters=[chap1, chap3],
        entities=[
            {"id": early_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Early"},
            {"id": late_entity_id,  "novel_id": novel["id"], "entity_type": "character", "name": "Late"},
        ],
        characters=[
            {"id": early_char_id, "entity_id": early_entity_id, "novel_id": novel["id"],
             "name": "Early", "aliases": [], "description": None, "first_appearance_chapter": 1},
            {"id": late_char_id,  "entity_id": late_entity_id,  "novel_id": novel["id"],
             "name": "Late",  "aliases": [], "description": None, "first_appearance_chapter": 3},
        ],
        relationships=[
            {"id": uuid4(), "entity_a_id": early_entity_id, "entity_b_id": late_entity_id,
             "rel_type": "knows", "from_chapter": 3, "to_chapter": None, "notes": None},
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/entity-graph?cap=1")
    assert response.status_code == 200
    body = response.json()
    labels = {n["label"] for n in body["nodes"]}
    assert "Late" not in labels
    assert "Early" in labels


def test_entity_graph_edges_cross_types(fake_db_factory, client):
    novel = make_novel()
    chap1 = make_chapter(novel["id"], 1)

    char_entity_id = uuid4()
    char_id = uuid4()
    loc_entity_id = uuid4()
    loc_id = uuid4()

    rel_id = uuid4()
    fake_db_factory(
        novels=[novel],
        chapters=[chap1],
        entities=[
            {"id": char_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Aria"},
            {"id": loc_entity_id,  "novel_id": novel["id"], "entity_type": "location",  "name": "Cave"},
        ],
        characters=[
            {"id": char_id, "entity_id": char_entity_id, "novel_id": novel["id"],
             "name": "Aria", "aliases": [], "description": None, "first_appearance_chapter": 1},
        ],
        locations=[
            {"id": loc_id, "entity_id": loc_entity_id, "novel_id": novel["id"], "name": "Cave"},
        ],
        relationships=[
            {"id": rel_id, "entity_a_id": char_entity_id, "entity_b_id": loc_entity_id,
             "rel_type": "hides in", "from_chapter": 1, "to_chapter": None, "notes": None},
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/entity-graph")
    assert response.status_code == 200
    body = response.json()
    assert len(body["edges"]) == 1
    edge = body["edges"][0]
    assert edge["label"] == "hides in"
    node_ids = {n["id"] for n in body["nodes"]}
    assert edge["from"] in node_ids
    assert edge["to"] in node_ids
