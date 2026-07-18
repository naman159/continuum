"""reads.graphs: cutoff-aware relationship/entity graph reads against real Postgres.

`relationship_graph` is the single implementation that used to be duplicated
between `api/queries.py::get_relationship_graph` (character-only graph, `{nodes,
edges}` shape backing the `RelationshipGraph` API schema) and
`mcp_server/queries.py::build_relationship_graph` (same query, `source`/`target`
edge keys, no cutoff-aware character filtering, plus an `up_to_chapter` echo).
This keeps the API's `{nodes, edges}` shape — `from`/`to`/`label` edge keys,
`GraphNode`/`GraphEdge`-compatible — as canonical, and adds the MCP variant's
extra fields (`notes`, `up_to_chapter`) additively so both surfaces read from
one query set.

`entity_graph` unifies characters/locations/objects/factions/custom entities
plus 5 kinds of edges (relationship, dynamic, event, possession, location);
story edges between the same pair collapse via `reads.common.merge_story_edges`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from reads.common import merge_story_edges, resolve_cutoff
from reads.relationship_types import resolve_symmetric


def relationship_graph(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> dict[str, Any]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)

    char_rows = db.fetchall(
        """
        SELECT c.id, e.name, c.first_appearance_chapter
        FROM entities e
        JOIN characters c ON c.entity_id = e.id
        WHERE e.novel_id = %s AND e.entity_type = 'character'
          AND (c.first_appearance_chapter IS NULL OR c.first_appearance_chapter <= %s)
        ORDER BY e.name
        """,
        (novel_id, cutoff),
        dict_rows=True,
    )
    nodes = [{"id": r["id"], "label": r["name"], "description": None} for r in char_rows]
    character_id_set = {n["id"] for n in nodes}

    rels_raw = db.fetchall(
        """
        SELECT r.id, ca.id AS char_a_id, cb.id AS char_b_id,
               r.rel_type, r.symmetric, r.from_chapter, r.notes
        FROM relationships r
        JOIN entities ea ON ea.id = r.entity_a_id AND ea.novel_id = %s AND ea.entity_type = 'character'
        JOIN entities eb ON eb.id = r.entity_b_id AND eb.entity_type = 'character'
        JOIN characters ca ON ca.entity_id = ea.id
        JOIN characters cb ON cb.entity_id = eb.id
        LEFT JOIN chapters rch ON rch.id = r.chapter_id
        WHERE COALESCE(r.from_chapter, rch.number, 0) <= %s
        ORDER BY r.from_chapter NULLS LAST, r.created_at
        """,
        (novel_id, cutoff),
        dict_rows=True,
    )
    edges = [
        {
            "id": r["id"],
            "from": r["char_a_id"],
            "to": r["char_b_id"],
            "label": r.get("rel_type"),
            "chapter_number": r.get("from_chapter"),
            "symmetric": resolve_symmetric(r.get("rel_type"), r.get("symmetric")),
            "notes": r.get("notes"),
        }
        for r in rels_raw
        if r["char_a_id"] in character_id_set and r["char_b_id"] in character_id_set
    ]

    # Only characters that actually appear in an edge are worth graphing —
    # ported as-is from the API's get_relationship_graph.
    edge_node_ids = {str(e["from"]) for e in edges} | {str(e["to"]) for e in edges}
    nodes = [n for n in nodes if str(n["id"]) in edge_node_ids]

    return {"nodes": nodes, "edges": edges, "up_to_chapter": cutoff}


def list_shared_dynamics(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    raw = db.fetchall(
        """
        SELECT sd.id, sd.entity_a_id, ea.name AS entity_a_name,
               sd.entity_b_id, eb.name AS entity_b_name,
               sd.description, ch.number AS chapter_number
        FROM shared_dynamics sd
        JOIN chapters ch ON ch.id = sd.chapter_id
        JOIN entities ea ON ea.id = sd.entity_a_id
        JOIN entities eb ON eb.id = sd.entity_b_id
        WHERE ch.novel_id = %s AND ch.number <= %s
        ORDER BY ch.number
        """,
        (novel_id, cutoff),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "entity_a_id": r["entity_a_id"],
            "entity_a_name": r["entity_a_name"],
            "entity_b_id": r["entity_b_id"],
            "entity_b_name": r["entity_b_name"],
            "chapter_number": r["chapter_number"],
            "description": r.get("description"),
        }
        for r in raw
    ]


def entity_graph(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> dict[str, Any]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)

    node_rows = db.fetchall(
        """
        SELECT e.id::text AS id,
               e.name AS label,
               e.entity_type,
               COALESCE(c.id, l.id, o.id, f.id, e.id)::text AS native_id,
               NULL::text AS description
        FROM entities e
        LEFT JOIN characters c ON c.entity_id = e.id
        LEFT JOIN locations  l ON l.entity_id = e.id
        LEFT JOIN objects    o ON o.entity_id = e.id
        LEFT JOIN factions   f ON f.entity_id = e.id
        WHERE e.novel_id = %s
          -- The typed LEFT JOINs are mutually exclusive, so COALESCE picks
          -- the one first_appearance anchor this entity has; factions and
          -- custom entities carry none and stay visible at every cutoff.
          AND (
            COALESCE(c.first_appearance_chapter, l.first_appearance_chapter,
                     o.first_appearance_chapter) IS NULL
            OR COALESCE(c.first_appearance_chapter, l.first_appearance_chapter,
                        o.first_appearance_chapter) <= %s
          )
        """,
        (novel_id, cutoff),
        dict_rows=True,
    )
    nodes = [dict(r) for r in node_rows]

    edge_rows = db.fetchall(
        """
        SELECT r.id::text AS id,
               r.entity_a_id::text AS "from",
               r.entity_b_id::text AS "to",
               r.rel_type AS label,
               r.symmetric AS symmetric,
               r.from_chapter AS chapter_number
        FROM relationships r
        JOIN entities ea ON ea.id = r.entity_a_id AND ea.novel_id = %s
        JOIN entities eb ON eb.id = r.entity_b_id AND eb.novel_id = %s
        LEFT JOIN chapters rch ON rch.id = r.chapter_id
        WHERE COALESCE(r.from_chapter, rch.number, 0) <= %s
        """,
        (novel_id, novel_id, cutoff),
        dict_rows=True,
    )
    edges = [dict(r) for r in edge_rows]
    for e in edges:
        e["edge_kind"] = "relationship"
        e["tooltip"] = None
        e["symmetric"] = resolve_symmetric(e.get("label"), e.get("symmetric"))

    raw_story: list[dict] = []

    dyn_rows = db.fetchall(
        """
        SELECT sd.entity_a_id::text AS "from",
               sd.entity_b_id::text AS "to",
               'dynamic'            AS edge_kind,
               sd.description       AS description
        FROM shared_dynamics sd
        JOIN chapters ch ON ch.id = sd.chapter_id
        JOIN entities ea ON ea.id = sd.entity_a_id AND ea.novel_id = %s
        JOIN entities eb ON eb.id = sd.entity_b_id AND eb.novel_id = %s
        WHERE ch.number <= %s
        """,
        (novel_id, novel_id, cutoff),
        dict_rows=True,
    )
    raw_story.extend(dict(r) for r in dyn_rows)

    ev_char_rows = db.fetchall(
        """
        SELECT ca.entity_id::text AS "from",
               cb.entity_id::text AS "to",
               'event'            AS edge_kind,
               e.description      AS description
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        JOIN characters ca ON ca.id = ANY(e.involved_characters)
        JOIN characters cb ON cb.id = ANY(e.involved_characters) AND cb.id > ca.id
        JOIN entities ea ON ea.id = ca.entity_id AND ea.novel_id = %s
        JOIN entities eb ON eb.id = cb.entity_id AND eb.novel_id = %s
        WHERE ch.number <= %s
        """,
        (novel_id, novel_id, cutoff),
        dict_rows=True,
    )
    raw_story.extend(dict(r) for r in ev_char_rows)

    ev_loc_rows = db.fetchall(
        """
        SELECT c.entity_id::text AS "from",
               l.entity_id::text AS "to",
               'event'           AS edge_kind,
               e.description     AS description
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        JOIN characters c ON c.id = ANY(e.involved_characters)
        JOIN locations  l ON l.id = ANY(e.involved_locations)
        JOIN entities ea ON ea.id = c.entity_id AND ea.novel_id = %s
        JOIN entities eb ON eb.id = l.entity_id AND eb.novel_id = %s
        WHERE ch.number <= %s
        """,
        (novel_id, novel_id, cutoff),
        dict_rows=True,
    )
    raw_story.extend(dict(r) for r in ev_loc_rows)

    poss_rows = db.fetchall(
        """
        SELECT c.entity_id::text AS "from",
               o.entity_id::text AS "to",
               'possession'      AS edge_kind,
               NULL::text        AS description
        FROM possesses_edges pe
        JOIN characters c ON c.id = pe.character_id
        JOIN objects    o ON o.id = pe.object_id
        JOIN entities ea ON ea.id = c.entity_id AND ea.novel_id = %s
        JOIN entities eb ON eb.id = o.entity_id AND eb.novel_id = %s
        WHERE (pe.since_chapter IS NULL OR pe.since_chapter <= %s)
          AND (pe.until_chapter IS NULL OR pe.until_chapter >= %s)
        """,
        (novel_id, novel_id, cutoff, cutoff),
        dict_rows=True,
    )
    raw_story.extend(dict(r) for r in poss_rows)

    loc_in_rows = db.fetchall(
        """
        SELECT lie.entity_id::text AS "from",
               l.entity_id::text   AS "to",
               'location'          AS edge_kind,
               NULL::text          AS description
        FROM located_in_edges lie
        JOIN locations l ON l.id = lie.location_id
        JOIN entities ea ON ea.id = lie.entity_id AND ea.novel_id = %s
        JOIN entities eb ON eb.id = l.entity_id   AND eb.novel_id = %s
        WHERE (lie.since_chapter IS NULL OR lie.since_chapter <= %s)
          AND (lie.until_chapter IS NULL OR lie.until_chapter >= %s)
        """,
        (novel_id, novel_id, cutoff, cutoff),
        dict_rows=True,
    )
    raw_story.extend(dict(r) for r in loc_in_rows)

    story_edges = merge_story_edges(raw_story)
    return {"nodes": nodes, "edges": edges + story_edges}
