"""reads.characters: cutoff-aware character reads against real Postgres.

`get_character_page` is the MCP writer tool's name-addressed entry point; it
resolves name -> character id in SQL and then delegates to
`get_character_detail` so the wiki detail page and the MCP character page
share one set of sub-queries instead of two drifting implementations.
"""

from __future__ import annotations

import difflib
from typing import Any
from uuid import UUID

from reads.common import resolve_cutoff
from reads.relationship_types import resolve_symmetric


def list_characters(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    rows = db.fetchall(
        """
        SELECT id, name, aliases, description, first_appearance_chapter
        FROM characters
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


def get_character_detail(
    db: Any, novel_id: UUID | str, character_id: UUID | str, up_to_chapter: int | None
) -> dict[str, Any] | None:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)

    identity_row = db.fetchone(
        """
        SELECT id, name, aliases, description, first_appearance_chapter, entity_id
        FROM characters WHERE id = %s AND novel_id = %s
        """,
        (character_id, novel_id),
        dict_rows=True,
    )
    if identity_row is None:
        return None
    first_appearance = identity_row.get("first_appearance_chapter")
    if first_appearance is not None and first_appearance > cutoff:
        # The character hasn't appeared yet as of this cutoff: reporting the
        # identity (name/description summarize later chapters) would leak
        # spoilers, and list_characters already hides them at the same cutoff.
        return None
    identity = {
        "id": identity_row["id"],
        "name": identity_row["name"],
        "aliases": list(identity_row.get("aliases") or []),
        "description": identity_row.get("description"),
        "first_appearance_chapter": identity_row.get("first_appearance_chapter"),
    }
    # Two id spaces are in play. events.involved_* store TYPED ids
    # (characters.id / locations.id / objects.id / factions.id): pipeline.py's
    # event insert uses resolver ResolvedEntity.entity_id, the type-specific
    # id. So the events query filters on `character_id` (the typed PK) and
    # involved_* names resolve via the typed tables. By contrast,
    # relationships/shared_dynamics entity_a/b_id are UNIVERSAL entities.id
    # (the pipeline uses ResolvedEntity.universal_id there), so those resolve
    # via characters.entity_id.
    char_entity_id = str(identity_row["entity_id"]) if identity_row.get("entity_id") else None

    states_rows = db.fetchall(
        """
        SELECT cs.*, ch.number AS chapter_number
        FROM character_states cs
        JOIN chapters ch ON ch.id = cs.chapter_id
        WHERE cs.character_id = %s AND ch.number <= %s
        ORDER BY ch.number
        """,
        (character_id, cutoff),
        dict_rows=True,
    )
    events_rows = db.fetchall(
        """
        SELECT e.id, e.description, e.event_type, e.impact_level,
               e.involved_characters, e.involved_locations, e.involved_objects, e.involved_factions,
               ch.number AS chapter_number
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        WHERE %s = ANY(e.involved_characters) AND ch.number <= %s
        ORDER BY ch.number, e.created_at
        """,
        (character_id, cutoff),
        dict_rows=True,
    )
    rels_rows = db.fetchall(
        """
        SELECT r.id, r.entity_a_id, r.entity_b_id, r.rel_type, r.symmetric,
               r.from_chapter, r.to_chapter, r.notes
        -- Endpoints are deliberately untyped. The `c.entity_id = ...` join
        -- already anchors one side to this character, so requiring
        -- entity_type='character' on BOTH sides only ever dropped the far
        -- endpoint — silently hiding every character↔object/location/faction
        -- relationship (a character's own sword, their faction membership).
        -- rel_to_row below already resolves the other endpoint's type for
        -- exactly this reason; that code was unreachable until now.
        FROM relationships r
        JOIN entities ea ON ea.id = r.entity_a_id
        JOIN entities eb ON eb.id = r.entity_b_id
        JOIN characters c ON c.entity_id = ea.id OR c.entity_id = eb.id
        LEFT JOIN chapters rch ON rch.id = r.chapter_id
        WHERE c.id = %s
          AND COALESCE(r.from_chapter, rch.number, 0) <= %s
          -- Still in force at the cutoff, not merely started before it.
          AND (r.to_chapter IS NULL OR r.to_chapter >= %s)
          AND r.superseded_by_id IS NULL
        """,
        (character_id, cutoff, cutoff),
        dict_rows=True,
    )

    # Typed-id -> name maps, one per typed table: events.involved_* store the
    # typed ids, and character_states.location_id FKs to locations(id) too.
    char_name_rows = db.fetchall(
        "SELECT id, name FROM characters WHERE novel_id = %s", (novel_id,), dict_rows=True
    )
    char_name = {r["id"]: r["name"] for r in char_name_rows}
    loc_rows = db.fetchall(
        "SELECT id, name FROM locations WHERE novel_id = %s", (novel_id,), dict_rows=True
    )
    location_name = {r["id"]: r["name"] for r in loc_rows}
    obj_rows = db.fetchall(
        "SELECT id, name FROM objects WHERE novel_id = %s", (novel_id,), dict_rows=True
    )
    object_name = {r["id"]: r["name"] for r in obj_rows}
    faction_rows = db.fetchall(
        "SELECT id, name FROM factions WHERE novel_id = %s", (novel_id,), dict_rows=True
    )
    faction_name = {r["id"]: r["name"] for r in faction_rows}

    states = [dict(r) for r in states_rows]
    events = [dict(r) for r in events_rows]
    rels = [dict(r) for r in rels_rows]

    def state_to_row(state: dict[str, Any]) -> dict[str, Any]:
        loc_id = state.get("location_id")
        return {
            "chapter_number": state.get("chapter_number"),
            "location": location_name.get(loc_id) if loc_id else None,
            "emotional_state": state.get("emotional_state"),
            "goals": state.get("goals"),
            "knowledge": list(state.get("knowledge") or []),
            "physical_state": state.get("physical_state"),
            "appearance": state.get("appearance"),
            "notes": state.get("notes"),
        }

    history = [state_to_row(s) for s in states]
    current_state = history[-1] if history else None

    def event_to_row(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": event["id"],
            "chapter_number": event.get("chapter_number"),
            "description": event.get("description"),
            "event_type": event.get("event_type"),
            "impact_level": event.get("impact_level"),
            "involved_characters": [char_name.get(cid, str(cid)) for cid in event.get("involved_characters") or []],
            "involved_locations": [location_name.get(lid, str(lid)) for lid in event.get("involved_locations") or []],
            "involved_objects": [object_name.get(oid, str(oid)) for oid in event.get("involved_objects") or []],
            "involved_factions": [faction_name.get(fid, str(fid)) for fid in (event.get("involved_factions") or [])],
        }

    def rel_to_row(rel: dict[str, Any]) -> dict[str, Any]:
        if str(rel["entity_a_id"]) == char_entity_id:
            other_universal = rel["entity_b_id"]
            direction = "from"
        else:
            other_universal = rel["entity_a_id"]
            direction = "to"
        other_entity = db.fetchone(
            "SELECT name, entity_type FROM entities WHERE id = %s",
            (other_universal,),
            dict_rows=True,
        )
        other_name = other_entity["name"] if other_entity else str(other_universal)
        other_type = other_entity["entity_type"] if other_entity else "character"
        return {
            "other_entity_name": other_name,
            "other_entity_type": other_type,
            "direction": direction,
            "symmetric": resolve_symmetric(rel.get("rel_type"), rel.get("symmetric")),
            "rel_type": rel.get("rel_type"),
            "from_chapter": rel.get("from_chapter"),
            "to_chapter": rel.get("to_chapter"),
            "notes": rel.get("notes"),
        }

    dyn_rows = db.fetchall(
        """
        SELECT sd.id, ch.number AS chapter_number,
               e_other.name AS other_entity_name,
               e_other.entity_type AS other_entity_type,
               sd.description
        FROM shared_dynamics sd
        JOIN chapters ch ON ch.id = sd.chapter_id
        JOIN characters c ON (c.entity_id = sd.entity_a_id OR c.entity_id = sd.entity_b_id)
        JOIN entities e_other ON e_other.id = (
            CASE WHEN c.entity_id = sd.entity_a_id THEN sd.entity_b_id ELSE sd.entity_a_id END
        )
        WHERE c.id = %s AND ch.novel_id = %s AND ch.number <= %s
        ORDER BY ch.number
        """,
        (character_id, novel_id, cutoff),
        dict_rows=True,
    )
    dynamics = [dict(r) for r in dyn_rows]

    return {
        "identity": identity,
        "current_state": current_state,
        "history": history,
        "relationships": [rel_to_row(r) for r in rels],
        "dynamics": dynamics,
        "events": [event_to_row(e) for e in events],
    }


def get_character_page(
    db: Any, novel_id: UUID | str, name: str, up_to_chapter: int | None
) -> dict[str, Any]:
    """Name-addressed character page for the MCP `get_character` tool.

    Resolves `name` to a character id in SQL, then reuses
    `get_character_detail` for every sub-query. Top-level output keys match
    the pre-migration `mcp_server.queries.build_character_page` shape
    (identity, current_state, history, relationships, events, spoiler_cap) —
    an MCP writing agent may depend on those keys.
    """
    character = db.fetchone(
        """
        SELECT id FROM characters
        WHERE novel_id = %s AND lower(name) = lower(%s)
        LIMIT 1
        """,
        (novel_id, name),
        dict_rows=True,
    )
    if character is None:
        rows = db.fetchall(
            "SELECT name FROM characters WHERE novel_id = %s",
            (novel_id,),
            dict_rows=True,
        )
        close = difflib.get_close_matches(name, [r["name"] for r in rows], n=3, cutoff=0.5)
        hint = f"; closest names: {', '.join(close)}" if close else ""
        raise ValueError(f"Character not found: {name}{hint}")

    detail = get_character_detail(db, novel_id, character["id"], up_to_chapter)
    if detail is None:
        # The row exists but first appears after the cutoff — to a writing
        # agent working at this chapter, the character does not exist yet.
        raise ValueError(f"Character not found: {name} (first appears after the writing chapter)")

    return {
        "identity": detail["identity"],
        "current_state": detail["current_state"],
        "history": detail["history"],
        "relationships": detail["relationships"],
        "events": detail["events"],
        "spoiler_cap": up_to_chapter,
    }
