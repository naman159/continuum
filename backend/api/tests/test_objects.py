from __future__ import annotations

from uuid import uuid4

from api.tests.conftest import make_chapter, make_novel


def _make_object(novel_id, name="the One Ring", **overrides):
    base = {
        "id": uuid4(),
        "novel_id": novel_id,
        "entity_id": uuid4(),
        "name": name,
        "aliases": [],
        "description": "A ring of power.",
        "significance": "MacGuffin",
        "first_appearance_chapter": 1,
    }
    base.update(overrides)
    return base


def test_list_objects(fake_db_factory, client):
    novel = make_novel()
    obj = _make_object(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        objects=[obj],
    )
    response = client.get(f"/api/novels/{novel['id']}/objects")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "the One Ring"
    assert body[0]["significance"] == "MacGuffin"


def test_list_objects_respects_cap(fake_db_factory, client):
    novel = make_novel()
    early = _make_object(novel["id"], name="sword", first_appearance_chapter=1)
    late = _make_object(novel["id"], name="shield", first_appearance_chapter=5)
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1), make_chapter(novel["id"], 5)],
        objects=[early, late],
    )
    response = client.get(f"/api/novels/{novel['id']}/objects?cap=3")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "sword"


def test_get_object_detail(fake_db_factory, client):
    novel = make_novel()
    obj = _make_object(novel["id"])
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        objects=[obj],
    )
    response = client.get(f"/api/novels/{novel['id']}/objects/{obj['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == "the One Ring"
    assert body["identity"]["significance"] == "MacGuffin"
    assert body["events"] == []
    assert body["characters"] == []
    assert body["relationships"] == []


def test_get_object_detail_includes_ownership(fake_db_factory, client):
    novel = make_novel()
    char_entity_id = uuid4()
    char_id = uuid4()
    obj_entity_id = uuid4()
    obj = _make_object(novel["id"], entity_id=obj_entity_id)
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        objects=[obj],
        characters=[{
            "id": char_id,
            "novel_id": novel["id"],
            "entity_id": char_entity_id,
            "name": "Frodo",
            "aliases": [],
            "description": None,
            "first_appearance_chapter": 1,
        }],
        relationships=[{
            "entity_a_id": char_entity_id,
            "entity_b_id": obj_entity_id,
            "rel_type": "carries",
            "from_chapter": 1,
            "to_chapter": None,
            "notes": None,
        }],
    )
    response = client.get(f"/api/novels/{novel['id']}/objects/{obj['id']}")
    assert response.status_code == 200
    body = response.json()
    assert len(body["relationships"]) == 1
    assert body["relationships"][0]["character_name"] == "Frodo"
    assert body["relationships"][0]["rel_type"] == "carries"


def test_get_object_detail_404(fake_db_factory, client):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[make_chapter(novel["id"], 1)])
    response = client.get(f"/api/novels/{novel['id']}/objects/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_object_detail_relationship_with_object_as_entity_a(fake_db_factory, client):
    """Relationships are found regardless of which side the object is stored on."""
    novel = make_novel()
    char_entity_id = uuid4()
    obj_entity_id = uuid4()
    obj = {
        "id": uuid4(),
        "novel_id": novel["id"],
        "entity_id": obj_entity_id,
        "name": "the One Ring",
        "aliases": [],
        "description": None,
        "significance": "corrupts its bearer",
        "first_appearance_chapter": 1,
    }
    character = {
        "id": uuid4(),
        "novel_id": novel["id"],
        "entity_id": char_entity_id,
        "name": "Frodo",
        "aliases": [],
        "description": None,
        "first_appearance_chapter": 1,
    }
    fake_db_factory(
        novels=[novel],
        chapters=[make_chapter(novel["id"], 1)],
        objects=[obj],
        characters=[character],
        relationships=[
            {
                "id": uuid4(),
                # Object stored as entity_a — previously invisible on the object page.
                "entity_a_id": obj_entity_id,
                "entity_b_id": char_entity_id,
                "rel_type": "carried_by",
                "from_chapter": 1,
                "to_chapter": None,
                "notes": None,
            }
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/objects/{obj['id']}")
    assert response.status_code == 200
    rels = response.json()["relationships"]
    assert len(rels) == 1
    assert rels[0]["character_name"] == "Frodo"
