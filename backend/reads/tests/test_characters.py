from __future__ import annotations

import uuid

import pytest

from reads import characters as characters_reads


def test_list_characters_cutoff_excludes_late_arrivals(db, seed_novel):
    seeded = seed_novel(db)  # factory: char A first appears ch1, char B ch3
    rows = characters_reads.list_characters(db, seeded["novel_id"], up_to_chapter=2)
    names = {r["name"] for r in rows}
    assert seeded["char_a_name"] in names
    assert seeded["char_b_name"] not in names


def test_list_characters_no_cutoff_includes_everyone(db, seed_novel):
    seeded = seed_novel(db)
    rows = characters_reads.list_characters(db, seeded["novel_id"], up_to_chapter=None)
    names = {r["name"] for r in rows}
    assert names == {seeded["char_a_name"], seeded["char_b_name"]}


def test_character_page_by_name_matches_detail(db, seed_novel):
    seeded = seed_novel(db)
    page = characters_reads.get_character_page(
        db, seeded["novel_id"], seeded["char_a_name"], up_to_chapter=2
    )
    assert page["identity"]["name"] == seeded["char_a_name"]
    detail = characters_reads.get_character_detail(
        db, seeded["novel_id"], seeded["char_a_id"], up_to_chapter=2
    )
    assert detail is not None
    # No history rows from beyond the cutoff in either shape.
    assert all(s["chapter_number"] <= 2 for s in detail["history"])
    assert all(s["chapter_number"] <= 2 for s in page["history"])
    assert page["current_state"] == detail["current_state"]
    assert page["relationships"] == detail["relationships"]
    assert page["events"] == detail["events"]
    assert page["spoiler_cap"] == 2


def test_character_page_top_level_keys_match_mcp_shape(db, seed_novel):
    """An MCP writing agent depends on exactly these top-level keys."""
    seeded = seed_novel(db)
    page = characters_reads.get_character_page(
        db, seeded["novel_id"], seeded["char_a_name"], up_to_chapter=None
    )
    assert set(page.keys()) == {
        "identity", "current_state", "history", "relationships", "events", "spoiler_cap",
    }


def test_character_page_not_found_suggests_close_names(db, seed_novel):
    seeded = seed_novel(db)
    with pytest.raises(ValueError, match="closest names"):
        characters_reads.get_character_page(db, seeded["novel_id"], "Aria Vanc", up_to_chapter=2)


def test_get_character_detail_returns_none_for_missing_character(db, seed_novel):
    seeded = seed_novel(db)
    result = characters_reads.get_character_detail(
        db, seeded["novel_id"], str(uuid.uuid4()), up_to_chapter=2
    )
    assert result is None


def test_character_detail_events_are_cutoff_capped_and_named(db, seed_novel):
    """Char A is involved in a ch1 discovery event and a ch3 faction event."""
    seeded = seed_novel(db)

    before = characters_reads.get_character_detail(
        db, seeded["novel_id"], seeded["char_a_id"], up_to_chapter=2
    )
    assert [e["chapter_number"] for e in before["events"]] == [1]
    assert before["events"][0]["involved_characters"] == [seeded["char_a_name"]]

    after = characters_reads.get_character_detail(
        db, seeded["novel_id"], seeded["char_a_id"], up_to_chapter=3
    )
    assert [e["chapter_number"] for e in after["events"]] == [1, 3]
    faction_event = after["events"][1]
    assert faction_event["involved_factions"] == [seeded["faction_name"]]


def test_character_detail_relationships_and_dynamics_respect_cutoff(db, seed_novel):
    """The A/B relationship and shared dynamic are both asserted in chapter 3."""
    seeded = seed_novel(db)

    before = characters_reads.get_character_detail(
        db, seeded["novel_id"], seeded["char_a_id"], up_to_chapter=2
    )
    assert before["relationships"] == []
    assert before["dynamics"] == []

    after = characters_reads.get_character_detail(
        db, seeded["novel_id"], seeded["char_a_id"], up_to_chapter=3
    )
    assert len(after["relationships"]) == 1
    assert after["relationships"][0]["other_entity_name"] == seeded["char_b_name"]
    assert len(after["dynamics"]) == 1
    assert after["dynamics"][0]["other_entity_name"] == seeded["char_b_name"]


def test_character_detail_current_state_location_resolves_by_name(db, seed_novel):
    seeded = seed_novel(db)
    detail = characters_reads.get_character_detail(
        db, seeded["novel_id"], seeded["char_a_id"], up_to_chapter=1
    )
    assert detail["current_state"]["location"] == seeded["loc_a_name"]
    assert detail["current_state"]["chapter_number"] == 1
