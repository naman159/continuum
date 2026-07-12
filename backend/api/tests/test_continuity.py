from __future__ import annotations


def test_continuity_resolved_filter(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    chap1_id, chap2_id = seeded["chapter_ids"][0], seeded["chapter_ids"][1]
    real_db.execute(
        """
        INSERT INTO continuity_flags (chapter_id, description, flag_type, resolved, resolved_chapter_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (chap1_id, "open flag", "foreshadowing", False, None),
    )
    real_db.execute(
        """
        INSERT INTO continuity_flags (chapter_id, description, flag_type, resolved, resolved_chapter_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (chap1_id, "resolved flag", "setup", True, chap2_id),
    )

    response = client.get(f"/api/novels/{seeded['novel_id']}/continuity?resolved=open")
    assert response.status_code == 200
    descriptions = [r["description"] for r in response.json()]
    assert descriptions == ["open flag"]


def test_list_critique_endpoint(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/continuity/critique")
    assert response.status_code == 200
    rows = response.json()
    assert [r["chapter_number"] for r in rows] == [2]
    assert rows[0] == {"chapter_number": 2, "passed": False, "fails": 1, "warns": 0}


def test_list_critique_endpoint_respects_cap(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/continuity/critique?cap=1")
    assert response.status_code == 200
    assert response.json() == []


def test_get_chapter_critique_endpoint(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/continuity/critique/2")
    assert response.status_code == 200
    body = response.json()
    assert body["chapter_number"] == 2
    assert body["passed"] is False
    assert len(body["findings"]) == 1
    assert body["findings"][0]["severity"] == "fail"


def test_get_chapter_critique_endpoint_404(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/continuity/critique/1")
    assert response.status_code == 404
