from __future__ import annotations


def test_list_shared_dynamics_returns_rows(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/dynamics")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["description"] == "Wary respect after the ambush."
    assert body[0]["chapter_number"] == 3
    assert body[0]["entity_a_name"] == seeded["char_a_name"]
    assert body[0]["entity_b_name"] == seeded["char_b_name"]


def test_list_shared_dynamics_respects_cap(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/dynamics?cap=2")
    assert response.status_code == 200
    assert response.json() == []
