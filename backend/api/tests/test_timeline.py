from __future__ import annotations


def test_timeline_returns_events_ordered_by_chapter(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/timeline")
    assert response.status_code == 200
    rows = response.json()
    assert [r["chapter_number"] for r in rows] == [1, 3]
    assert rows[0]["description"] == "Aria discovers the ledger's trail."
    assert rows[0]["involved_characters"] == [seeded["char_a_name"]]


def test_timeline_cap_filters_chapters(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/timeline?cap=1")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["chapter_number"] == 1
    assert rows[0]["description"] == "Aria discovers the ledger's trail."


def test_timeline_involved_entities_resolve_to_names(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/timeline")
    assert response.status_code == 200
    ch3_event = next(r for r in response.json() if r["chapter_number"] == 3)
    assert ch3_event["involved_characters"] == [seeded["char_a_name"]]
    assert ch3_event["involved_locations"] == [seeded["loc_b_name"]]
    assert ch3_event["involved_objects"] == [seeded["obj_name"]]
    assert ch3_event["involved_factions"] == [seeded["faction_name"]]


def test_timeline_excludes_other_novels(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    seed_novel_real(real_db)  # a second, unrelated novel
    response = client.get(f"/api/novels/{seeded['novel_id']}/timeline")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
