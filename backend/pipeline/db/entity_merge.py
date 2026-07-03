from __future__ import annotations

"""Merge one entity into another, repairing wrong dedup decisions.

All statements run in a single transaction. Source and target must belong to
the same novel and share an entity_type. After the merge the source's name and
aliases become aliases of the target, so the resolver keeps resolving old
references to the surviving entity.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class EntityMergeError(ValueError):
    pass


_TYPED_TABLE = {
    "character": "characters",
    "location": "locations",
    "object": "objects",
    "faction": "factions",
}

_INVOLVED_COLUMN = {
    "character": "involved_characters",
    "location": "involved_locations",
    "object": "involved_objects",
    "faction": "involved_factions",
}


def _fetch_entity(cur, entity_id: str, novel_id: str) -> dict[str, Any]:
    cur.execute(
        "SELECT id, entity_type, name, aliases FROM entities WHERE id = %s AND novel_id = %s",
        (entity_id, novel_id),
    )
    row = cur.fetchone()
    if row is None:
        raise EntityMergeError(f"entity {entity_id} not found in novel {novel_id}")
    if isinstance(row, dict):
        return row
    return {"id": row[0], "entity_type": row[1], "name": row[2], "aliases": list(row[3] or [])}


def _fetch_typed(cur, table: str, entity_id: str) -> dict[str, Any] | None:
    cur.execute(
        f"SELECT id, name, aliases FROM {table} WHERE entity_id = %s LIMIT 1",
        (entity_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return {"id": row[0], "name": row[1], "aliases": list(row[2] or [])}


def _replace_in_array_column(
    cur, table: str, column: str, src: str, tgt: str,
    *, extra_where: str = "", extra_params: tuple = (),
) -> None:
    cur.execute(
        f"""
        UPDATE {table}
           SET {column} = ARRAY(
                 SELECT DISTINCT x
                   FROM unnest(array_replace({column}, %s::uuid, %s::uuid)) AS x
               )
         WHERE %s::uuid = ANY({column}) {extra_where}
        """,
        (src, tgt, src, *extra_params),
    )


def merge_entities(
    db: Any, *, novel_id: str, source_entity_id: str, target_entity_id: str
) -> dict[str, Any]:
    if str(source_entity_id) == str(target_entity_id):
        raise EntityMergeError("source and target are the same entity")

    with db.transaction() as cur:
        source = _fetch_entity(cur, str(source_entity_id), str(novel_id))
        target = _fetch_entity(cur, str(target_entity_id), str(novel_id))
        if source["entity_type"] != target["entity_type"]:
            raise EntityMergeError(
                f"type mismatch: {source['entity_type']} vs {target['entity_type']}"
            )
        entity_type = str(source["entity_type"])
        src, tgt = str(source["id"]), str(target["id"])

        # ---- entity-level references (apply to every type) ----
        # A pre-existing src<->tgt relationship would become a self-pair during
        # the repoint and trip CHECK (entity_a_id <> entity_b_id) at UPDATE
        # time — remove those rows first.
        cur.execute(
            """
            DELETE FROM relationships
             WHERE (entity_a_id = %s AND entity_b_id = %s)
                OR (entity_a_id = %s AND entity_b_id = %s)
            """,
            (src, tgt, tgt, src),
        )
        cur.execute("UPDATE relationships SET entity_a_id = %s WHERE entity_a_id = %s", (tgt, src))
        cur.execute("UPDATE relationships SET entity_b_id = %s WHERE entity_b_id = %s", (tgt, src))
        # Repointing can leave duplicate (pair, rel_type) edges (one originally
        # tgt<->X, one src<->X). Keep the oldest, drop the rest, matching the
        # persist-time dedupe invariant.
        cur.execute(
            """
            DELETE FROM relationships a
             USING relationships b
             WHERE a.id <> b.id
               AND a.rel_type IS NOT DISTINCT FROM b.rel_type
               AND LEAST(a.entity_a_id::text, a.entity_b_id::text) = LEAST(b.entity_a_id::text, b.entity_b_id::text)
               AND GREATEST(a.entity_a_id::text, a.entity_b_id::text) = GREATEST(b.entity_a_id::text, b.entity_b_id::text)
               AND (a.entity_a_id = %s OR a.entity_b_id = %s)
               AND (b.entity_a_id = %s OR b.entity_b_id = %s)
               AND (a.created_at > b.created_at
                    OR (a.created_at = b.created_at AND a.id::text > b.id::text))
            """,
            (tgt, tgt, tgt, tgt),
        )

        # shared_dynamics: UNIQUE(a, b, chapter) — handle collisions row by row.
        cur.execute(
            "SELECT id, entity_a_id, entity_b_id, chapter_id FROM shared_dynamics "
            "WHERE entity_a_id = %s OR entity_b_id = %s",
            (src, src),
        )
        for row in cur.fetchall():
            r = row if isinstance(row, dict) else {
                "id": row[0], "entity_a_id": row[1], "entity_b_id": row[2], "chapter_id": row[3]
            }
            new_a = tgt if str(r["entity_a_id"]) == src else str(r["entity_a_id"])
            new_b = tgt if str(r["entity_b_id"]) == src else str(r["entity_b_id"])
            if new_a == new_b:
                cur.execute("DELETE FROM shared_dynamics WHERE id = %s", (r["id"],))
                continue
            cur.execute(
                "SELECT id FROM shared_dynamics WHERE entity_a_id = %s AND entity_b_id = %s "
                "AND chapter_id = %s AND id <> %s",
                (new_a, new_b, r["chapter_id"], r["id"]),
            )
            if cur.fetchone() is not None:
                cur.execute("DELETE FROM shared_dynamics WHERE id = %s", (r["id"],))
            else:
                cur.execute(
                    "UPDATE shared_dynamics SET entity_a_id = %s, entity_b_id = %s WHERE id = %s",
                    (new_a, new_b, r["id"]),
                )

        # canon_facts: drop source facts whose predicate the target already has.
        # knows_edges.fact_id references canon_facts with NO ACTION; repoint
        # edges from soon-to-be-deleted source facts to the target's
        # same-predicate twin before the collision delete below.
        cur.execute(
            """
            UPDATE knows_edges k
               SET fact_id = t.id
              FROM canon_facts s
              JOIN canon_facts t
                ON t.subject_entity_id = %s AND t.predicate = s.predicate
             WHERE s.subject_entity_id = %s
               AND k.fact_id = s.id
            """,
            (tgt, src),
        )
        cur.execute(
            """
            DELETE FROM canon_facts s
             WHERE s.subject_entity_id = %s
               AND EXISTS (
                 SELECT 1 FROM canon_facts t
                  WHERE t.subject_entity_id = %s AND t.predicate = s.predicate
               )
            """,
            (src, tgt),
        )
        cur.execute(
            "UPDATE canon_facts SET subject_entity_id = %s WHERE subject_entity_id = %s",
            (tgt, src),
        )

        _replace_in_array_column(
            cur, "commitments", "related_entity_ids", src, tgt,
            extra_where="AND novel_id = %s", extra_params=(str(novel_id),),
        )
        cur.execute("UPDATE located_in_edges SET entity_id = %s WHERE entity_id = %s", (tgt, src))
        cur.execute("UPDATE events SET subject_entity_id = %s WHERE subject_entity_id = %s", (tgt, src))
        cur.execute("UPDATE events SET object_entity_id = %s WHERE object_entity_id = %s", (tgt, src))

        # ---- typed-table references ----
        table = _TYPED_TABLE.get(entity_type)
        if table is not None:
            src_typed = _fetch_typed(cur, table, src)
            tgt_typed = _fetch_typed(cur, table, tgt)
            if src_typed is None or tgt_typed is None:
                raise EntityMergeError(f"typed rows missing for {entity_type} merge")
            st, tt = str(src_typed["id"]), str(tgt_typed["id"])

            _replace_in_array_column(cur, "events", _INVOLVED_COLUMN[entity_type], st, tt)

            if entity_type == "character":
                cur.execute("UPDATE character_states SET character_id = %s WHERE character_id = %s", (tt, st))
                cur.execute("UPDATE scenes SET pov_character_id = %s WHERE pov_character_id = %s", (tt, st))
                _replace_in_array_column(cur, "scenes", "present_characters", st, tt)
                cur.execute("UPDATE knows_edges SET character_id = %s WHERE character_id = %s", (tt, st))
                _replace_in_array_column(cur, "knows_edges", "shared_with", st, tt)
                cur.execute("UPDATE possesses_edges SET character_id = %s WHERE character_id = %s", (tt, st))
            elif entity_type == "location":
                cur.execute("UPDATE character_states SET location_id = %s WHERE location_id = %s", (tt, st))
                cur.execute("UPDATE scenes SET location_id = %s WHERE location_id = %s", (tt, st))
                cur.execute("UPDATE locations SET parent_location_id = %s WHERE parent_location_id = %s", (tt, st))
                cur.execute("UPDATE located_in_edges SET location_id = %s WHERE location_id = %s", (tt, st))
            elif entity_type == "object":
                cur.execute("UPDATE possesses_edges SET object_id = %s WHERE object_id = %s", (tt, st))

            # alias union: target absorbs source's name + aliases.
            merged_aliases = list(dict.fromkeys(
                [*(tgt_typed.get("aliases") or []),
                 *(src_typed.get("aliases") or []),
                 str(src_typed.get("name") or source["name"])]
            ))
            merged_aliases = [
                a for a in merged_aliases
                if a and a.lower() != str(tgt_typed.get("name", "")).lower()
            ]
            cur.execute(
                f"UPDATE {table} SET aliases = %s WHERE id = %s",
                (merged_aliases, tt),
            )
            cur.execute(f"DELETE FROM {table} WHERE id = %s", (st,))
        else:
            # custom type: aliases live on the entities row.
            merged_aliases = list(dict.fromkeys(
                [*(target.get("aliases") or []), *(source.get("aliases") or []), str(source["name"])]
            ))
            merged_aliases = [
                a for a in merged_aliases if a and a.lower() != str(target["name"]).lower()
            ]
            cur.execute(
                "UPDATE entities SET aliases = %s WHERE id = %s",
                (merged_aliases, tgt),
            )

        cur.execute("DELETE FROM entities WHERE id = %s", (src,))

    logger.info("merged entity %s into %s (%s)", src, tgt, entity_type)
    return {
        "source_entity_id": src,
        "target_entity_id": tgt,
        "entity_type": entity_type,
        "absorbed_name": str(source["name"]),
    }


__all__ = ["merge_entities", "EntityMergeError"]
