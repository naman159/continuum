from __future__ import annotations


def test_list_factions(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/factions")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == seeded["faction_name"]


def test_list_factions_respects_cap_via_earliest_event_mention(seed_novel_real, real_db, client):
    """Faction visibility is derived from the earliest event involving it —
    the seed faction's only event is in ch3, so cap=1 hides it."""
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/factions?cap=1")
    assert response.status_code == 200
    assert response.json() == []
    response = client.get(f"/api/novels/{seeded['novel_id']}/factions?cap=3")
    assert response.status_code == 200
    assert [row["name"] for row in response.json()] == [seeded["faction_name"]]


def test_get_faction_detail(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/factions/{seeded['faction_id']}?cap=2")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == seeded["faction_name"]
    assert body["events"] == []
    assert body["characters"] == []


def test_get_faction_detail_events_beyond_cap(seed_novel_real, real_db, client):
    """The faction-involving event lands in ch3."""
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/factions/{seeded['faction_id']}")
    assert response.status_code == 200
    body = response.json()
    assert [e["chapter_number"] for e in body["events"]] == [3]
    assert body["events"][0]["involved_characters"] == [seeded["char_a_name"]]


def test_get_faction_detail_404(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(
        f"/api/novels/{seeded['novel_id']}/factions/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
