from __future__ import annotations


def test_list_chapters_filters_by_cap(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/chapters?cap=2")
    assert response.status_code == 200
    body = response.json()
    numbers = [r["number"] for r in body]
    assert numbers == [1, 2]


def test_list_chapters_carries_critique_summary(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/chapters?cap=2")
    assert response.status_code == 200
    body = response.json()
    ch1 = next(r for r in body if r["number"] == 1)
    ch2 = next(r for r in body if r["number"] == 2)
    assert ch1["critique"] is None
    assert ch2["critique"] == {"passed": False, "fails": 1, "warns": 0}
