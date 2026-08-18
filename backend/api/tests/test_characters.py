from __future__ import annotations


def test_list_characters(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/characters")
    assert response.status_code == 200
    body = response.json()
    names = {row["name"] for row in body}
    assert names == {seeded["char_a_name"], seeded["char_b_name"]}
    aria = next(row for row in body if row["name"] == seeded["char_a_name"])
    assert aria["aliases"] == []
    assert aria["first_appearance_chapter"] == 1


def test_list_characters_respects_cap(seed_novel_real, real_db, client):
    """Characters whose first_appearance_chapter > cap are excluded."""
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/characters?cap=2")
    assert response.status_code == 200
    names = [row["name"] for row in response.json()]
    assert names == [seeded["char_a_name"]]


def test_character_detail_includes_states_within_cap(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(
        f"/api/novels/{seeded['novel_id']}/characters/{seeded['char_a_id']}?cap=2"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["name"] == seeded["char_a_name"]
    assert len(body["history"]) == 1
    assert body["history"][0]["chapter_number"] == 1
    assert body["current_state"]["chapter_number"] == 1
    # Only the ch1 discovery event is within the cap; the ch3 faction event
    # and the ch3 relationship/dynamic with char B are excluded.
    assert len(body["events"]) == 1
    assert body["events"][0]["chapter_number"] == 1
    assert body["relationships"] == []
    assert body["dynamics"] == []


def test_character_detail_includes_events_and_relationships_beyond_cap(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/characters/{seeded['char_a_id']}")
    assert response.status_code == 200
    body = response.json()
    assert [e["chapter_number"] for e in body["events"]] == [1, 3]
    by_name = {r["other_entity_name"]: r for r in body["relationships"]}
    assert seeded["char_b_name"] in by_name
    # Cross-type edges surface here too — see reads/tests/test_characters.py
    # for why this used to be silently dropped.
    assert by_name["Iron Compass"]["other_entity_type"] == "object"
    assert len(body["dynamics"]) == 1
    assert body["dynamics"][0]["other_entity_name"] == seeded["char_b_name"]


def test_character_detail_404(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(
        f"/api/novels/{seeded['novel_id']}/characters/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
