from __future__ import annotations


def test_patch_canon_fact_sets_lock(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.patch(
        f"/api/novels/{seeded['novel_id']}/canon/{seeded['canon_fact_id']}",
        json={"locked": True},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    facts = client.get(f"/api/novels/{seeded['novel_id']}/canon").json()
    mine = next(f for f in facts if f["id"] == seeded["canon_fact_id"])
    assert mine["locked"] is True


def test_patch_canon_fact_missing_returns_404(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.patch(
        f"/api/novels/{seeded['novel_id']}/canon/00000000-0000-0000-0000-000000000000",
        json={"locked": True},
    )
    assert response.status_code == 404


def test_patch_canon_fact_nothing_to_update_returns_422(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.patch(
        f"/api/novels/{seeded['novel_id']}/canon/{seeded['canon_fact_id']}",
        json={},
    )
    assert response.status_code == 422


def test_create_canon_fact_inserts(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.post(
        f"/api/novels/{seeded['novel_id']}/canon",
        json={
            "subject_entity_id": seeded["char_a_eid"],
            "predicate": "hair_color",
            "value": "black",
            "kind": "physical",
            "locked": True,
        },
    )
    assert response.status_code == 201
    fact_id = response.json()["id"]

    facts = client.get(f"/api/novels/{seeded['novel_id']}/canon").json()
    mine = next(f for f in facts if f["id"] == fact_id)
    assert mine["value"] == "black"
    assert mine["locked"] is True
    assert mine["kind"] == "physical"


def test_delete_canon_fact(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.delete(f"/api/novels/{seeded['novel_id']}/canon/{seeded['canon_fact_id']}")
    assert response.status_code == 204

    facts = client.get(f"/api/novels/{seeded['novel_id']}/canon").json()
    assert not any(f["id"] == seeded["canon_fact_id"] for f in facts)


def test_delete_canon_fact_missing_returns_404(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.delete(
        f"/api/novels/{seeded['novel_id']}/canon/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404


def test_create_canon_rejects_subject_from_another_novel(seed_novel_real, real_db, client):
    first = seed_novel_real(real_db)
    second = seed_novel_real(real_db)
    response = client.post(
        f"/api/novels/{first['novel_id']}/canon",
        json={"subject_entity_id": second["char_a_eid"], "predicate": "hair_color", "value": "black"},
    )
    assert response.status_code == 404
    assert real_db.fetchval(
        "SELECT count(*) FROM canon_facts WHERE novel_id = %s AND subject_entity_id = %s",
        (first["novel_id"], second["char_a_eid"]),
    ) == 0
