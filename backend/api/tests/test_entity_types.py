from __future__ import annotations


def test_list_genres(fake_db_factory, client):
    fake_db_factory()
    response = client.get("/api/genres")
    assert response.status_code == 200
    body = response.json()
    ids = {g["id"] for g in body}
    assert "litrpg" in ids
    assert "high_fantasy" in ids


def test_create_novel_with_entity_types(fake_db_factory, client):
    fake_db_factory()
    response = client.post("/api/novels", json={
        "title": "My LitRPG",
        "custom_entity_types": [
            {"name": "realm", "description": "A distinct universe."},
            {"name": "power_system", "description": "Named ability system."},
        ],
    })
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "My LitRPG"


def test_create_novel_no_custom_types(fake_db_factory, client):
    fake_db_factory()
    response = client.post("/api/novels", json={"title": "Plain Novel"})
    assert response.status_code == 201


def test_list_entity_types(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    real_db.execute(
        "INSERT INTO novel_entity_types (novel_id, name, description) VALUES (%s,%s,%s)",
        (seeded["novel_id"], "realm", "A dimension."),
    )
    real_db.execute(
        "INSERT INTO novel_entity_types (novel_id, name, description) VALUES (%s,%s,%s)",
        (seeded["novel_id"], "power_system", "Ability system."),
    )
    response = client.get(f"/api/novels/{seeded['novel_id']}/entity-types")
    assert response.status_code == 200
    body = response.json()
    names = {t["name"] for t in body}
    assert names == {"realm", "power_system"}


def test_list_entity_types_ignores_cap(seed_novel_real, real_db, client):
    """list_entity_types has no cap parameter at all — the route doesn't accept one."""
    seeded = seed_novel_real(real_db)
    real_db.execute(
        "INSERT INTO novel_entity_types (novel_id, name, description) VALUES (%s,%s,%s)",
        (seeded["novel_id"], "realm", "A dimension."),
    )
    response = client.get(f"/api/novels/{seeded['novel_id']}/entity-types?cap=1")
    assert response.status_code == 200
    assert {t["name"] for t in response.json()} == {"realm"}


def test_list_custom_entities(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    real_db.execute(
        "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s)",
        (seeded["novel_id"], "realm", "The 93rd Universe"),
    )
    response = client.get(f"/api/novels/{seeded['novel_id']}/entity-types/realm/entities")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "The 93rd Universe"
    assert body[0]["entity_type"] == "realm"


def test_get_custom_entity_detail(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    row = real_db.fetchone(
        "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
        (seeded["novel_id"], "realm", "The 93rd Universe"),
        dict_rows=True,
        commit=True,
    )
    realm_entity_id = row["id"]
    real_db.execute(
        "INSERT INTO relationships (entity_a_id, entity_b_id, rel_type, from_chapter) VALUES (%s,%s,%s,%s)",
        (seeded["char_a_eid"], realm_entity_id, "inhabits", 1),
    )
    response = client.get(f"/api/novels/{seeded['novel_id']}/custom-entities/{realm_entity_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "The 93rd Universe"
    assert body["entity_type"] == "realm"
    assert len(body["relationships"]) == 1
    assert body["relationships"][0]["rel_type"] == "inhabits"
    assert body["relationships"][0]["other_entity_name"] == seeded["char_a_name"]


def test_get_custom_entity_detail_relationship_excluded_beyond_cap(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    row = real_db.fetchone(
        "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
        (seeded["novel_id"], "realm", "The 93rd Universe"),
        dict_rows=True,
        commit=True,
    )
    realm_entity_id = row["id"]
    real_db.execute(
        "INSERT INTO relationships (entity_a_id, entity_b_id, rel_type, from_chapter) VALUES (%s,%s,%s,%s)",
        (seeded["char_a_eid"], realm_entity_id, "inhabits", 3),
    )
    response = client.get(f"/api/novels/{seeded['novel_id']}/custom-entities/{realm_entity_id}?cap=1")
    assert response.status_code == 200
    assert response.json()["relationships"] == []


def test_get_custom_entity_detail_404(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(
        f"/api/novels/{seeded['novel_id']}/custom-entities/00000000-0000-0000-0000-000000000001"
    )
    assert response.status_code == 404
