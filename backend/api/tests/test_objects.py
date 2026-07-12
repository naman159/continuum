from __future__ import annotations


def test_list_objects(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/objects")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == seeded["obj_name"]
    assert body[0]["first_appearance_chapter"] == 1


def test_get_object_detail(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/objects/{seeded['obj_id']}?cap=2")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == seeded["obj_name"]
    assert body["events"] == []
    assert body["characters"] == []
    assert body["relationships"] == []


def test_get_object_detail_events_and_characters_beyond_cap(seed_novel_real, real_db, client):
    """The ch3 faction event touches the Iron Compass."""
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/objects/{seeded['obj_id']}")
    assert response.status_code == 200
    body = response.json()
    assert [e["chapter_number"] for e in body["events"]] == [3]
    assert body["characters"] == [seeded["char_a_name"]]


def test_get_object_detail_404(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(
        f"/api/novels/{seeded['novel_id']}/objects/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404


def test_object_detail_relationship_with_object_as_entity_a(seed_novel_real, real_db, client):
    """Relationships are found regardless of which side the object is stored on."""
    seeded = seed_novel_real(real_db)
    real_db.execute(
        "INSERT INTO relationships (entity_a_id, entity_b_id, rel_type, from_chapter) VALUES (%s,%s,%s,%s)",
        (seeded["obj_eid"], seeded["char_b_eid"], "carried_by", 1),
    )
    response = client.get(f"/api/novels/{seeded['novel_id']}/objects/{seeded['obj_id']}")
    assert response.status_code == 200
    rels = response.json()["relationships"]
    assert len(rels) == 1
    assert rels[0]["character_name"] == seeded["char_b_name"]
    assert rels[0]["rel_type"] == "carried_by"
