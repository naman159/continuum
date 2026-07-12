"""reads.world: cutoff-aware location/object/faction/custom-entity reads
against real Postgres.

`list_entity_types` is the one function in this module exempt from the
cutoff contract: `novel_entity_types` is a type registry (config data
describing what custom entity types a novel uses), not chapter-anchored
story data, so it takes no `up_to_chapter` parameter and always returns
every registered type.

`list_factions` / `list_custom_entities` accept `up_to_chapter` for
interface symmetry with the other list functions, but the underlying rows
(factions, custom entities) carry no chapter anchor of their own — the
parameter goes unused there. `get_faction_detail` / `get_custom_entity_detail`
do use it: entity identity has no chapter anchor, but the chapter-anchored
sub-lists inside each detail (events for factions; relationships for custom
entities) apply the cutoff so spoilers past `up_to_chapter` stay hidden.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from reads.common import resolve_cutoff, resolve_symmetric


def _typed_name_maps(
    db: Any, novel_id: UUID | str
) -> tuple[dict[Any, str], dict[Any, str], dict[Any, str], dict[Any, str]]:
    """Typed-id -> name maps for the four typed entity tables.

    events.involved_characters/locations/objects/factions store TYPED table
    ids (characters.id / locations.id / objects.id / factions.id) — the
    resolver's per-type id, not the universal entities.id — so name
    resolution for event rows must go through these maps.
    """
    char_rows = db.fetchall(
        "SELECT id, name FROM characters WHERE novel_id = %s", (novel_id,), dict_rows=True
    )
    loc_rows = db.fetchall(
        "SELECT id, name FROM locations WHERE novel_id = %s", (novel_id,), dict_rows=True
    )
    obj_rows = db.fetchall(
        "SELECT id, name FROM objects WHERE novel_id = %s", (novel_id,), dict_rows=True
    )
    faction_rows = db.fetchall(
        "SELECT id, name FROM factions WHERE novel_id = %s", (novel_id,), dict_rows=True
    )
    return (
        {r["id"]: r["name"] for r in char_rows},
        {r["id"]: r["name"] for r in loc_rows},
        {r["id"]: r["name"] for r in obj_rows},
        {r["id"]: r["name"] for r in faction_rows},
    )


def _event_row(
    r: dict[str, Any],
    char_name: dict[Any, str],
    loc_name: dict[Any, str],
    obj_name: dict[Any, str],
    faction_name: dict[Any, str],
) -> dict[str, Any]:
    return {
        "id": r["id"],
        "chapter_number": r["chapter_number"],
        "description": r["description"],
        "event_type": r.get("event_type"),
        "impact_level": r.get("impact_level"),
        "involved_characters": [char_name.get(cid, str(cid)) for cid in (r.get("involved_characters") or [])],
        "involved_locations": [loc_name.get(lid, str(lid)) for lid in (r.get("involved_locations") or [])],
        "involved_objects": [obj_name.get(oid, str(oid)) for oid in (r.get("involved_objects") or [])],
        "involved_factions": [faction_name.get(fid, str(fid)) for fid in (r.get("involved_factions") or [])],
    }


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def list_locations(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    rows = db.fetchall(
        """
        SELECT id, name, aliases, description, first_appearance_chapter
        FROM locations
        WHERE novel_id = %s
          AND (first_appearance_chapter IS NULL OR first_appearance_chapter <= %s)
        ORDER BY name
        """,
        (novel_id, cutoff),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "aliases": list(r.get("aliases") or []),
            "description": r.get("description"),
            "first_appearance_chapter": r.get("first_appearance_chapter"),
        }
        for r in rows
    ]


def get_location_detail(
    db: Any, novel_id: UUID | str, location_id: UUID | str, up_to_chapter: int | None
) -> dict[str, Any] | None:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)

    row = db.fetchone(
        """
        SELECT id, name, aliases, description, first_appearance_chapter
        FROM locations WHERE novel_id = %s AND id = %s
        """,
        (novel_id, location_id),
        dict_rows=True,
    )
    if row is None:
        return None

    char_name, loc_name, obj_name, faction_name = _typed_name_maps(db, novel_id)

    event_rows = db.fetchall(
        """
        SELECT e.id, e.description, e.event_type, e.impact_level,
               ch.number AS chapter_number,
               e.involved_characters, e.involved_locations, e.involved_objects, e.involved_factions
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
          AND %s = ANY(e.involved_locations)
        ORDER BY ch.number
        """,
        (novel_id, cutoff, location_id),
        dict_rows=True,
    )
    events = [_event_row(r, char_name, loc_name, obj_name, faction_name) for r in event_rows]

    char_rows = db.fetchall(
        """
        SELECT DISTINCT c.name
        FROM character_states cs
        JOIN characters c ON c.id = cs.character_id
        JOIN chapters ch ON ch.id = cs.chapter_id
        WHERE c.novel_id = %s AND cs.location_id = %s AND ch.number <= %s
        ORDER BY c.name
        """,
        (novel_id, location_id, cutoff),
        dict_rows=True,
    )
    characters = [r["name"] for r in char_rows]

    return {
        "identity": {
            "id": row["id"],
            "name": row["name"],
            "aliases": list(row.get("aliases") or []),
            "description": row.get("description"),
            "first_appearance_chapter": row.get("first_appearance_chapter"),
        },
        "events": events,
        "characters": characters,
    }


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------


def list_objects(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    rows = db.fetchall(
        """
        SELECT id, name, aliases, description, significance, first_appearance_chapter
        FROM objects
        WHERE novel_id = %s
          AND (first_appearance_chapter IS NULL OR first_appearance_chapter <= %s)
        ORDER BY name
        """,
        (novel_id, cutoff),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "aliases": list(r.get("aliases") or []),
            "description": r.get("description"),
            "significance": r.get("significance"),
            "first_appearance_chapter": r.get("first_appearance_chapter"),
        }
        for r in rows
    ]


def get_object_detail(
    db: Any, novel_id: UUID | str, object_id: UUID | str, up_to_chapter: int | None
) -> dict[str, Any] | None:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)

    row = db.fetchone(
        """
        SELECT id, name, aliases, description, significance,
               first_appearance_chapter, entity_id
        FROM objects WHERE novel_id = %s AND id = %s
        """,
        (novel_id, object_id),
        dict_rows=True,
    )
    if row is None:
        return None

    char_name, loc_name, obj_name, faction_name = _typed_name_maps(db, novel_id)

    event_rows = db.fetchall(
        """
        SELECT e.id, e.description, e.event_type, e.impact_level,
               ch.number AS chapter_number,
               e.involved_characters, e.involved_locations, e.involved_objects, e.involved_factions
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
          AND %s = ANY(e.involved_objects)
        ORDER BY ch.number
        """,
        (novel_id, cutoff, object_id),
        dict_rows=True,
    )
    events = [_event_row(r, char_name, loc_name, obj_name, faction_name) for r in event_rows]

    char_ids_in_events: set[Any] = set()
    for r in event_rows:
        char_ids_in_events.update(r.get("involved_characters") or [])
    characters = sorted(char_name.get(cid, str(cid)) for cid in char_ids_in_events)

    # relationships.entity_a_id/entity_b_id are UNIVERSAL entities.id (the
    # pipeline writes ResolvedEntity.universal_id there), so this join goes
    # through objects.entity_id rather than the typed objects.id used above.
    rel_rows = db.fetchall(
        """
        SELECT c.name AS character_name, r.rel_type, r.from_chapter, r.to_chapter, r.notes
        FROM relationships r
        JOIN objects ob ON ob.id = %s AND ob.novel_id = %s
        JOIN characters c
          ON c.novel_id = %s
         AND c.entity_id = CASE
               WHEN r.entity_a_id = ob.entity_id THEN r.entity_b_id
               ELSE r.entity_a_id
             END
        WHERE r.entity_a_id = ob.entity_id OR r.entity_b_id = ob.entity_id
        ORDER BY r.from_chapter NULLS LAST
        """,
        (object_id, novel_id, novel_id),
        dict_rows=True,
    )
    relationships = [dict(r) for r in rel_rows]

    return {
        "identity": {
            "id": row["id"],
            "name": row["name"],
            "aliases": list(row.get("aliases") or []),
            "description": row.get("description"),
            "significance": row.get("significance"),
            "first_appearance_chapter": row.get("first_appearance_chapter"),
        },
        "events": events,
        "characters": characters,
        "relationships": relationships,
    }


# ---------------------------------------------------------------------------
# Factions
# ---------------------------------------------------------------------------


def list_factions(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> list[dict[str, Any]]:
    """Faction rows carry no chapter anchor; up_to_chapter is accepted for
    interface symmetry with the other world-entity listers but unused here."""
    rows = db.fetchall(
        """
        SELECT id, name, aliases, description
        FROM factions
        WHERE novel_id = %s
        ORDER BY name
        """,
        (novel_id,),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "aliases": list(r.get("aliases") or []),
            "description": r.get("description"),
        }
        for r in rows
    ]


def get_faction_detail(
    db: Any, novel_id: UUID | str, faction_id: UUID | str, up_to_chapter: int | None
) -> dict[str, Any] | None:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)

    row = db.fetchone(
        """
        SELECT id, name, aliases, description
        FROM factions WHERE novel_id = %s AND id = %s
        """,
        (novel_id, faction_id),
        dict_rows=True,
    )
    if row is None:
        return None

    char_name, loc_name, obj_name, faction_name = _typed_name_maps(db, novel_id)

    # events.involved_factions stores factions.id (the typed-table id the
    # resolver returns), not entities.id — query with the faction id itself.
    event_rows = db.fetchall(
        """
        SELECT e.id, e.description, e.event_type, e.impact_level,
               ch.number AS chapter_number,
               e.involved_characters, e.involved_locations, e.involved_objects, e.involved_factions
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
          AND %s = ANY(e.involved_factions)
        ORDER BY ch.number
        """,
        (novel_id, cutoff, faction_id),
        dict_rows=True,
    )
    events = [_event_row(r, char_name, loc_name, obj_name, faction_name) for r in event_rows]

    return {
        "identity": {
            "id": row["id"],
            "name": row["name"],
            "aliases": list(row.get("aliases") or []),
            "description": row.get("description"),
        },
        "events": events,
        "characters": [],
    }


# ---------------------------------------------------------------------------
# Entity types / custom entities
# ---------------------------------------------------------------------------


def list_entity_types(db: Any, novel_id: UUID | str) -> list[dict[str, Any]]:
    """Cutoff-exempt: novel_entity_types is a type registry (config), not
    chapter-anchored story data — there is no up_to_chapter parameter."""
    rows = db.fetchall(
        "SELECT id, novel_id, name, description FROM novel_entity_types WHERE novel_id = %s ORDER BY name",
        (novel_id,),
        dict_rows=True,
    )
    return [dict(r) for r in rows]


def list_custom_entities(
    db: Any, novel_id: UUID | str, entity_type: str, up_to_chapter: int | None
) -> list[dict[str, Any]]:
    """Custom entity rows carry no chapter anchor; up_to_chapter is accepted
    for interface symmetry with the other world-entity listers but unused
    here."""
    rows = db.fetchall(
        """
        SELECT id, name, entity_type, NULL AS description
        FROM entities
        WHERE novel_id = %s AND entity_type = %s
        ORDER BY name
        """,
        (novel_id, entity_type),
        dict_rows=True,
    )
    return [
        {"id": str(r["id"]), "name": r["name"], "entity_type": r["entity_type"], "description": r.get("description")}
        for r in rows
    ]


def get_custom_entity_detail(
    db: Any, novel_id: UUID | str, entity_id: UUID | str, up_to_chapter: int | None
) -> dict[str, Any] | None:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)

    row = db.fetchone(
        "SELECT id, name, entity_type FROM entities WHERE id = %s AND novel_id = %s",
        (entity_id, novel_id),
        dict_rows=True,
    )
    if row is None:
        return None

    # relationships.entity_a_id/entity_b_id are UNIVERSAL entities.id, and a
    # custom entity's own id IS its entities.id (custom types have no typed
    # side-table), so this filters directly on entity_id with no join needed.
    # from_chapter is this sub-list's chapter anchor, so the cutoff applies.
    rels_rows = db.fetchall(
        """
        SELECT r.entity_a_id, r.entity_b_id, r.rel_type, r.symmetric, r.from_chapter, r.to_chapter, r.notes,
               ea.name AS name_a, ea.entity_type AS type_a,
               eb.name AS name_b, eb.entity_type AS type_b
        FROM relationships r
        JOIN entities ea ON ea.id = r.entity_a_id
        JOIN entities eb ON eb.id = r.entity_b_id
        WHERE (r.entity_a_id = %s OR r.entity_b_id = %s)
          AND (r.from_chapter IS NULL OR r.from_chapter <= %s)
        """,
        (entity_id, entity_id, cutoff),
        dict_rows=True,
    )
    relationships = []
    for r in rels_rows:
        if str(r["entity_a_id"]) == str(entity_id):
            other_name, other_type, direction = r["name_b"], r["type_b"], "from"
        else:
            other_name, other_type, direction = r["name_a"], r["type_a"], "to"
        relationships.append({
            "other_entity_name": other_name,
            "other_entity_type": other_type,
            "direction": direction,
            "symmetric": resolve_symmetric(r.get("rel_type"), r.get("symmetric")),
            "rel_type": r.get("rel_type"),
            "from_chapter": r.get("from_chapter"),
            "to_chapter": r.get("to_chapter"),
            "notes": r.get("notes"),
        })

    return {
        "id": str(row["id"]),
        "name": row["name"],
        "entity_type": row["entity_type"],
        "description": None,
        "relationships": relationships,
    }
