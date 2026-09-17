"""reads.knowledge: cutoff-aware theory-of-mind, location/possession, and
canon-fact reads against real Postgres. Moved from `api/queries.py`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from reads.common import resolve_cutoff


def list_knows_edges(
    db: Any,
    novel_id: UUID | str,
    up_to_chapter: int | None,
    character_id: UUID | str | None,
) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    where = ["c.novel_id = %s", "k.learned_chapter <= %s", "k.superseded_by_id IS NULL"]
    params: list[Any] = [novel_id, cutoff]
    if character_id is not None:
        where.append("k.character_id = %s")
        params.append(character_id)
    rows = db.fetchall(
        f"""
        SELECT k.id, k.character_id, c.name AS character_name,
               k.fact_description, k.learned_chapter, k.source_type,
               k.source_event_id, k.certainty, k.shared_with
          FROM knows_edges k
          JOIN characters c ON c.id = k.character_id
         WHERE {' AND '.join(where)}
         ORDER BY k.learned_chapter, c.name
        """,
        tuple(params),
        dict_rows=True,
    )
    all_shared_ids = sorted({str(cid) for r in rows for cid in (r["shared_with"] or [])})
    name_by_id: dict[str, str] = {}
    if all_shared_ids:
        chars = db.fetchall(
            "SELECT id, name FROM characters WHERE id = ANY(%s::uuid[])",
            (all_shared_ids,),
            dict_rows=True,
        )
        name_by_id = {str(c["id"]): c["name"] for c in chars}
    return [
        {
            "id": r["id"],
            "character_id": r["character_id"],
            "character_name": r["character_name"],
            "fact_description": r["fact_description"],
            "learned_chapter": r["learned_chapter"],
            "source_type": r["source_type"],
            "source_event_id": r["source_event_id"],
            "certainty": float(r["certainty"]) if r["certainty"] is not None else None,
            "shared_with_names": [
                name_by_id.get(str(cid), str(cid)) for cid in (r["shared_with"] or [])
            ],
        }
        for r in rows
    ]


def list_location_edges(
    db: Any, novel_id: UUID | str, up_to_chapter: int | None, active_only: bool
) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    where = ["e.novel_id = %s", "le.since_chapter <= %s"]
    params: list[Any] = [novel_id, cutoff]
    if active_only:
        # A later move does not erase the location at an earlier cutoff.
        # Same-chapter moves still collapse to the final location.
        where.append("""NOT EXISTS (
            SELECT 1 FROM located_in_edges newer
            WHERE newer.id = le.superseded_by_id AND newer.since_chapter <= %s
        )""")
        params.append(cutoff)
        where.append("(le.until_chapter IS NULL OR le.until_chapter >= %s)")
        params.append(cutoff)
    rows = db.fetchall(
        f"""
        SELECT le.id, le.entity_id, e.name AS entity_name, e.entity_type,
               le.location_id, loc.name AS location_name,
               le.since_chapter, le.until_chapter, le.certainty
          FROM located_in_edges le
          JOIN entities e ON e.id = le.entity_id
          LEFT JOIN locations loc ON loc.id = le.location_id
         WHERE {' AND '.join(where)}
         ORDER BY le.since_chapter, e.name
        """,
        tuple(params),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "entity_id": r["entity_id"],
            "entity_name": r["entity_name"],
            "entity_type": r["entity_type"],
            "location_id": r["location_id"],
            "location_name": r["location_name"],
            "since_chapter": r["since_chapter"],
            "until_chapter": r["until_chapter"] if r["until_chapter"] is not None and r["until_chapter"] <= cutoff else None,
            "certainty": float(r["certainty"]) if r["certainty"] is not None else None,
        }
        for r in rows
    ]


def list_possession_edges(
    db: Any, novel_id: UUID | str, up_to_chapter: int | None, active_only: bool
) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    where = ["c.novel_id = %s", "pe.since_chapter <= %s"]
    params: list[Any] = [novel_id, cutoff]
    if active_only:
        where.append("pe.superseded_by_id IS NULL")
        # Possession replay records the LOSS chapter, unlike location edges
        # whose end is the last chapter at the previous location.
        where.append("(pe.until_chapter IS NULL OR pe.until_chapter > %s)")
        params.append(cutoff)
    rows = db.fetchall(
        f"""
        SELECT pe.id, pe.character_id, c.name AS character_name,
               pe.object_id, o.name AS object_name,
               pe.since_chapter, pe.until_chapter, pe.certainty
          FROM possesses_edges pe
          JOIN characters c ON c.id = pe.character_id
          LEFT JOIN objects o ON o.id = pe.object_id
         WHERE {' AND '.join(where)}
         ORDER BY pe.since_chapter, c.name
        """,
        tuple(params),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "character_id": r["character_id"],
            "character_name": r["character_name"],
            "object_id": r["object_id"],
            "object_name": r["object_name"],
            "since_chapter": r["since_chapter"],
            "until_chapter": r["until_chapter"] if r["until_chapter"] is not None and r["until_chapter"] <= cutoff else None,
            "certainty": float(r["certainty"]) if r["certainty"] is not None else None,
        }
        for r in rows
    ]


def list_canon_facts(
    db: Any, novel_id: UUID | str, up_to_chapter: int | None, locked_only: bool
) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    where = ["cf.novel_id = %s", "(cf.source_chapter IS NULL OR cf.source_chapter <= %s)"]
    params: list[Any] = [novel_id, cutoff]
    if locked_only:
        where.append("cf.locked = true")
    rows = db.fetchall(
        f"""
        SELECT cf.id, cf.kind, cf.subject_entity_id, e.name AS subject_name,
               cf.predicate, cf.value, cf.source_chapter, cf.confidence, cf.locked
          FROM canon_facts cf
          LEFT JOIN entities e ON e.id = cf.subject_entity_id
         WHERE {' AND '.join(where)}
         ORDER BY cf.locked DESC, e.name NULLS LAST, cf.predicate
        """,
        tuple(params),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "subject_entity_id": r["subject_entity_id"],
            "subject_name": r["subject_name"],
            "predicate": r["predicate"],
            "value": r["value"],
            "source_chapter": r["source_chapter"],
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            "locked": bool(r["locked"]),
        }
        for r in rows
    ]
