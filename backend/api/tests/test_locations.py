from __future__ import annotations


def test_list_locations(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/locations")
    assert response.status_code == 200
    body = response.json()
    names = {row["name"] for row in body}
    assert names == {seeded["loc_a_name"], seeded["loc_b_name"]}
    fogmere = next(row for row in body if row["name"] == seeded["loc_a_name"])
    assert fogmere["aliases"] == []
    assert fogmere["first_appearance_chapter"] == 1


def test_list_locations_respects_cap(seed_novel_real, real_db, client):
    """loc_b (Sable Archive) first-appears ch3 and is excluded by cap=1."""
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/locations?cap=1")
    assert response.status_code == 200
    names = [row["name"] for row in response.json()]
    assert names == [seeded["loc_a_name"]]


def test_get_location_detail(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/locations/{seeded['loc_a_id']}?cap=1")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == seeded["loc_a_name"]
    assert body["events"] == []
    assert body["characters"] == [seeded["char_a_name"]]


def test_get_location_detail_events_beyond_cap(seed_novel_real, real_db, client):
    """The ch3 faction event touches loc_b (Sable Archive)."""
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/locations/{seeded['loc_b_id']}")
    assert response.status_code == 200
    body = response.json()
    assert [e["chapter_number"] for e in body["events"]] == [3]
    assert body["events"][0]["involved_factions"] == [seeded["faction_name"]]


def test_get_location_detail_404(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(
        f"/api/novels/{seeded['novel_id']}/locations/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
