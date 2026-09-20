"""reads.timeline: cutoff-aware story-event timeline reads against real Postgres.

Chapter events grouped chronologically (by chapter, then insertion order within
the chapter) — ported from `api/queries.py::list_timeline`'s real-DB branch.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pipeline.db.history import metadata_table

from reads.common import resolve_cutoff


def list_timeline(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    raw = db.fetchall(
        """
        SELECT e.id, e.description, e.event_type, e.impact_level,
               e.involved_characters, e.involved_locations, e.involved_objects, e.involved_factions,
               ch.number AS chapter_number
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
        ORDER BY ch.number, e.created_at
        """,
        (novel_id, cutoff),
        dict_rows=True,
    )

    # events.involved_* store TYPED ids (characters.id / locations.id /
    # objects.id / factions.id) — see reads/characters.py's module docstring
    # for the two-id-space explanation. Name resolution goes through the
    # typed tables, same as reads.world._typed_name_maps.
    char_rows = db.fetchall(f"SELECT id, name FROM {metadata_table('characters', novel_id, cutoff)} characters WHERE novel_id = %s", (novel_id,), dict_rows=True)
    char_name = {r["id"]: r["name"] for r in char_rows}
    loc_rows = db.fetchall(f"SELECT id, name FROM {metadata_table('locations', novel_id, cutoff)} locations WHERE novel_id = %s", (novel_id,), dict_rows=True)
    loc_name = {r["id"]: r["name"] for r in loc_rows}
    obj_rows = db.fetchall(f"SELECT id, name FROM {metadata_table('objects', novel_id, cutoff)} objects WHERE novel_id = %s", (novel_id,), dict_rows=True)
    obj_name = {r["id"]: r["name"] for r in obj_rows}
    faction_rows = db.fetchall(f"SELECT id, name FROM {metadata_table('factions', novel_id, cutoff)} factions WHERE novel_id = %s", (novel_id,), dict_rows=True)
    faction_name = {r["id"]: r["name"] for r in faction_rows}

    return [
        {
            "id": r["id"],
            "chapter_number": r["chapter_number"],
            "description": r["description"],
            "event_type": r.get("event_type"),
            "impact_level": r.get("impact_level"),
            "involved_characters": [char_name.get(c, str(c)) for c in (r.get("involved_characters") or [])],
            "involved_locations": [loc_name.get(l, str(l)) for l in (r.get("involved_locations") or [])],
            "involved_objects": [obj_name.get(o, str(o)) for o in (r.get("involved_objects") or [])],
            "involved_factions": [faction_name.get(fid, str(fid)) for fid in (r.get("involved_factions") or [])],
        }
        for r in raw
    ]
