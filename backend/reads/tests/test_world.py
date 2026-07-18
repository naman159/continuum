from __future__ import annotations

import uuid

from reads import world as world_reads


def test_locations_and_objects_respect_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    locs = world_reads.list_locations(db, seeded["novel_id"], up_to_chapter=3)
    assert locs
    objs = world_reads.list_objects(db, seeded["novel_id"], up_to_chapter=3)
    assert objs


def test_faction_detail_sublists_apply_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    detail = world_reads.get_faction_detail(
        db, seeded["novel_id"], seeded["faction_id"], up_to_chapter=1
    )
    assert detail is not None
    for key, rows in detail.items():
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and "chapter_number" in row:
                    assert row["chapter_number"] <= 1, key


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def test_list_locations_excludes_late_arrivals(db, seed_novel):
    """loc_a first-appears ch1, loc_b first-appears ch3."""
    seeded = seed_novel(db)
    names = {r["name"] for r in world_reads.list_locations(db, seeded["novel_id"], up_to_chapter=1)}
    assert names == {seeded["loc_a_name"]}
    names_all = {r["name"] for r in world_reads.list_locations(db, seeded["novel_id"], up_to_chapter=3)}
    assert names_all == {seeded["loc_a_name"], seeded["loc_b_name"]}


def test_get_location_detail_events_respect_cutoff(db, seed_novel):
    """The ch3 faction event touches loc_b (Sable Archive)."""
    seeded = seed_novel(db)

    before = world_reads.get_location_detail(db, seeded["novel_id"], seeded["loc_b_id"], up_to_chapter=2)
    assert before is not None
    assert before["events"] == []

    after = world_reads.get_location_detail(db, seeded["novel_id"], seeded["loc_b_id"], up_to_chapter=3)
    assert [e["chapter_number"] for e in after["events"]] == [3]
    assert after["events"][0]["involved_factions"] == [seeded["faction_name"]]
    assert after["identity"]["name"] == seeded["loc_b_name"]


def test_get_location_detail_characters_respect_cutoff(db, seed_novel):
    """char A's location delta places them at loc_a in ch1."""
    seeded = seed_novel(db)
    detail = world_reads.get_location_detail(db, seeded["novel_id"], seeded["loc_a_id"], up_to_chapter=1)
    assert detail["characters"] == [seeded["char_a_name"]]


def test_get_location_detail_404(db, seed_novel):
    seeded = seed_novel(db)
    result = world_reads.get_location_detail(db, seeded["novel_id"], str(uuid.uuid4()), up_to_chapter=3)
    assert result is None


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------


def test_list_objects_excludes_late_arrivals(db, seed_novel):
    seeded = seed_novel(db)
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
            (seeded["novel_id"], "object", "Late Lantern"),
        )
        late_entity_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO objects (novel_id, entity_id, name, first_appearance_chapter) "
            "VALUES (%s,%s,%s,%s)",
            (seeded["novel_id"], late_entity_id, "Late Lantern", 3),
        )

    early_only = {r["name"] for r in world_reads.list_objects(db, seeded["novel_id"], up_to_chapter=1)}
    assert early_only == {seeded["obj_name"]}
    both = {r["name"] for r in world_reads.list_objects(db, seeded["novel_id"], up_to_chapter=3)}
    assert both == {seeded["obj_name"], "Late Lantern"}


def test_get_object_detail_events_and_characters_respect_cutoff(db, seed_novel):
    """The ch3 faction event touches the Iron Compass."""
    seeded = seed_novel(db)

    before = world_reads.get_object_detail(db, seeded["novel_id"], seeded["obj_id"], up_to_chapter=2)
    assert before is not None
    assert before["events"] == []
    assert before["characters"] == []

    after = world_reads.get_object_detail(db, seeded["novel_id"], seeded["obj_id"], up_to_chapter=3)
    assert [e["chapter_number"] for e in after["events"]] == [3]
    assert after["characters"] == [seeded["char_a_name"]]
    assert after["identity"]["name"] == seeded["obj_name"]


def test_get_object_detail_404(db, seed_novel):
    seeded = seed_novel(db)
    result = world_reads.get_object_detail(db, seeded["novel_id"], str(uuid.uuid4()), up_to_chapter=3)
    assert result is None


def test_get_object_detail_relationship_matches_either_direction(db, seed_novel):
    """Regression: relationships must be found whether the object is stored
    as entity_a or entity_b — a previous version only checked entity_b and
    silently hid half of an object's relationships. (The seed factory already
    plants one object relationship at from_chapter=3, so up_to_chapter=None
    — the latest chapter — sees both it and this test's own row.)"""
    seeded = seed_novel(db)
    db.execute(
        "INSERT INTO relationships (entity_a_id, entity_b_id, rel_type, from_chapter) VALUES (%s,%s,%s,%s)",
        (seeded["obj_eid"], seeded["char_b_eid"], "carried_by", 1),
    )
    detail = world_reads.get_object_detail(db, seeded["novel_id"], seeded["obj_id"], up_to_chapter=None)
    carried = [r for r in detail["relationships"] if r["rel_type"] == "carried_by"]
    assert len(carried) == 1
    assert carried[0]["character_name"] == seeded["char_b_name"]


def test_get_object_detail_relationships_respect_cutoff(db, seed_novel):
    """The seed factory's object relationship (Iron Compass <-> Aria) carries
    from_chapter=3, so it must be hidden below that cutoff and visible at it."""
    seeded = seed_novel(db)

    before = world_reads.get_object_detail(db, seeded["novel_id"], seeded["obj_id"], up_to_chapter=2)
    assert before is not None
    assert before["relationships"] == []

    after = world_reads.get_object_detail(db, seeded["novel_id"], seeded["obj_id"], up_to_chapter=3)
    assert len(after["relationships"]) == 1
    rel = after["relationships"][0]
    assert rel["character_name"] == seeded["char_a_name"]
    assert rel["rel_type"] == "entrusted_to"
    assert rel["from_chapter"] == 3


# ---------------------------------------------------------------------------
# Factions
# ---------------------------------------------------------------------------


def test_list_factions_derives_visibility_from_earliest_event_mention(db, seed_novel):
    """Factions carry no first_appearance anchor, so visibility is derived
    from the earliest event that involves them (seed faction: ch3). A faction
    never mentioned in any event has no derivable anchor and stays visible."""
    seeded = seed_novel(db)
    early = {r["name"] for r in world_reads.list_factions(db, seeded["novel_id"], up_to_chapter=2)}
    assert seeded["faction_name"] not in early
    late = {r["name"] for r in world_reads.list_factions(db, seeded["novel_id"], up_to_chapter=None)}
    assert seeded["faction_name"] in late

    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,'faction','Unseen Court') RETURNING id",
            (seeded["novel_id"],),
        )
        eid = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO factions (novel_id, entity_id, name) VALUES (%s,%s,'Unseen Court')",
            (seeded["novel_id"], eid),
        )
    unmentioned = {r["name"] for r in world_reads.list_factions(db, seeded["novel_id"], up_to_chapter=0)}
    assert "Unseen Court" in unmentioned


def test_list_custom_entities_derives_visibility_from_earliest_relationship(db, seed_novel):
    seeded = seed_novel(db)
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,'spell','Emberward') RETURNING id",
            (seeded["novel_id"],),
        )
        spell_eid = str(cur.fetchone()[0])
        # First referenced by a ch3-asserted relationship (from_chapter NULL →
        # provenance chapter anchors it).
        cur.execute(
            "INSERT INTO relationships (entity_a_id, entity_b_id, rel_type, from_chapter, chapter_id)"
            " VALUES (%s,%s,'wields',NULL,%s)",
            (seeded["char_a_eid"], spell_eid, seeded["chapter_ids"][2]),
        )
        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,'spell','Nameless Rite')",
            (seeded["novel_id"],),
        )
    early = {r["name"] for r in world_reads.list_custom_entities(db, seeded["novel_id"], "spell", up_to_chapter=2)}
    assert "Emberward" not in early
    assert "Nameless Rite" in early  # no references anywhere: no derivable anchor, stays visible
    late = {r["name"] for r in world_reads.list_custom_entities(db, seeded["novel_id"], "spell", up_to_chapter=None)}
    assert "Emberward" in late


def test_get_faction_detail_events_respect_cutoff(db, seed_novel):
    seeded = seed_novel(db)

    before = world_reads.get_faction_detail(db, seeded["novel_id"], seeded["faction_id"], up_to_chapter=2)
    assert before["events"] == []

    after = world_reads.get_faction_detail(db, seeded["novel_id"], seeded["faction_id"], up_to_chapter=3)
    assert [e["chapter_number"] for e in after["events"]] == [3]
    assert after["events"][0]["involved_characters"] == [seeded["char_a_name"]]


def test_get_faction_detail_404(db, seed_novel):
    seeded = seed_novel(db)
    result = world_reads.get_faction_detail(db, seeded["novel_id"], str(uuid.uuid4()), up_to_chapter=3)
    assert result is None


# ---------------------------------------------------------------------------
# Entity types / custom entities
# ---------------------------------------------------------------------------


def test_list_entity_types_has_no_cutoff_parameter(db, seed_novel):
    seeded = seed_novel(db)
    db.execute(
        "INSERT INTO novel_entity_types (novel_id, name, description) VALUES (%s,%s,%s)",
        (seeded["novel_id"], "realm", "A distinct universe."),
    )
    rows = world_reads.list_entity_types(db, seeded["novel_id"])
    assert [r["name"] for r in rows] == ["realm"]


def test_list_custom_entities_filters_by_type(db, seed_novel):
    seeded = seed_novel(db)
    db.execute(
        "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s)",
        (seeded["novel_id"], "realm", "The 93rd Universe"),
    )
    rows = world_reads.list_custom_entities(db, seeded["novel_id"], "realm", up_to_chapter=None)
    assert len(rows) == 1
    assert rows[0]["name"] == "The 93rd Universe"
    assert rows[0]["entity_type"] == "realm"
    assert world_reads.list_custom_entities(db, seeded["novel_id"], "power_system", up_to_chapter=None) == []


def test_get_custom_entity_detail_relationships_respect_cutoff(db, seed_novel):
    """A custom entity's relationship to char A is asserted starting ch3."""
    seeded = seed_novel(db)
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
            (seeded["novel_id"], "realm", "The 93rd Universe"),
        )
        realm_entity_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO relationships (entity_a_id, entity_b_id, rel_type, from_chapter, chapter_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (seeded["char_a_eid"], realm_entity_id, "bound_to", 3, seeded["chapter_ids"][2]),
        )

    before = world_reads.get_custom_entity_detail(db, seeded["novel_id"], realm_entity_id, up_to_chapter=2)
    assert before is not None
    assert before["relationships"] == []

    after = world_reads.get_custom_entity_detail(db, seeded["novel_id"], realm_entity_id, up_to_chapter=3)
    assert len(after["relationships"]) == 1
    rel = after["relationships"][0]
    assert rel["other_entity_name"] == seeded["char_a_name"]
    assert rel["other_entity_type"] == "character"
    assert rel["rel_type"] == "bound_to"


def test_get_custom_entity_detail_404(db, seed_novel):
    seeded = seed_novel(db)
    result = world_reads.get_custom_entity_detail(db, seeded["novel_id"], str(uuid.uuid4()), up_to_chapter=3)
    assert result is None
