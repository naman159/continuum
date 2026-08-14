from __future__ import annotations

"""Merge one entity into another, repairing wrong dedup decisions.

All statements run in a single transaction. Source and target must belong to
the same novel. After the merge the source's name and aliases become aliases of
the target, so the resolver keeps resolving old references to the surviving
entity.

Cross-type merges are supported and mean something different from same-type
ones. A same-type merge says "these two rows are the same thing"; a cross-type
merge says "the source was *misclassified*, and the target's type is correct."
Extraction produces these constantly — the same LitRPG skill filed as a
character in one chapter and an object in the next — and until they could be
merged there was no way to repair one at all, because the canonicalizer loads
its candidate roster one entity_type at a time and never compares across types.

The distinction matters for what survives. Entity-level references (relationships,
shared_dynamics, canon_facts, commitments, state_deltas) key off `entities.id`
and repoint identically either way. Type-specific rows do not: a
`character_states` row hanging off something that turned out to be an object is
an artifact of the misclassification, not data worth migrating, so it dies with
the source's typed row. Event involvement *is* migrated, moving from the
source's `involved_*` column into the target's.
"""

import logging
from typing import Any

from pipeline.entity_tables import TYPED_TABLES

logger = logging.getLogger(__name__)


class EntityMergeError(ValueError):
    pass



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


def _move_between_array_columns(
    cur, table: str, from_column: str, to_column: str, src: str, tgt: str
) -> None:
    """Move a typed id out of one array column and into another on the same row.

    Used for `events.involved_*` when the merge crosses types: the event still
    involves the entity, it was just filed under the wrong kind of involvement.
    """
    cur.execute(
        f"""
        UPDATE {table}
           SET {from_column} = array_remove({from_column}, %s::uuid),
               {to_column} = ARRAY(
                 SELECT DISTINCT x
                   FROM unnest(array_append(coalesce({to_column}, '{{}}'::uuid[]), %s::uuid)) AS x
               )
         WHERE %s::uuid = ANY({from_column})
        """,
        (src, tgt, src),
    )


def _detach_typed_references(cur, entity_type: str, typed_id: str) -> None:
    """Clear references to a typed row that will not survive a cross-type merge.

    Only the FKs that are *not* ON DELETE CASCADE need this — `scenes.pov_character_id`,
    `scenes.location_id`, `character_states.location_id` and
    `locations.parent_location_id` would otherwise raise a foreign-key violation
    when the source's typed row is deleted. All are nullable, and the reference
    is wrong data anyway once the row is known to be a misclassification.
    """
    if entity_type == "character":
        cur.execute(
            "UPDATE scenes SET pov_character_id = NULL WHERE pov_character_id = %s",
            (typed_id,),
        )
        cur.execute(
            "UPDATE scenes SET present_characters = array_remove(present_characters, %s::uuid) "
            "WHERE %s::uuid = ANY(present_characters)",
            (typed_id, typed_id),
        )
    elif entity_type == "location":
        cur.execute(
            "UPDATE locations SET parent_location_id = NULL WHERE parent_location_id = %s",
            (typed_id,),
        )
        cur.execute("UPDATE scenes SET location_id = NULL WHERE location_id = %s", (typed_id,))
        cur.execute(
            "UPDATE character_states SET location_id = NULL WHERE location_id = %s",
            (typed_id,),
        )


def _union_aliases(target_name: str, existing: list, absorbed: list) -> list[str]:
    merged = list(dict.fromkeys([*(existing or []), *(absorbed or [])]))
    return [a for a in merged if a and a.lower() != str(target_name or "").lower()]


def merge_entities(
    db: Any, *, novel_id: str, source_entity_id: str, target_entity_id: str
) -> dict[str, Any]:
    if str(source_entity_id) == str(target_entity_id):
        raise EntityMergeError("source and target are the same entity")

    with db.transaction() as cur:
        source = _fetch_entity(cur, str(source_entity_id), str(novel_id))
        target = _fetch_entity(cur, str(target_entity_id), str(novel_id))
        source_type = str(source["entity_type"])
        entity_type = str(target["entity_type"])
        cross_type = source_type != entity_type
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

        # state_deltas.subject_id/object_id reference entities(id) directly (not a
        # typed table) and CASCADE on delete — repoint before the source entity is
        # dropped, or tier-2 extraction data (possession/knowledge/status deltas)
        # is silently destroyed.
        cur.execute("UPDATE state_deltas SET subject_id = %s WHERE subject_id = %s", (tgt, src))
        cur.execute("UPDATE state_deltas SET object_id = %s WHERE object_id = %s", (tgt, src))

        # ---- typed-table references ----
        table = TYPED_TABLES.get(entity_type)
        source_table = TYPED_TABLES.get(source_type)

        if cross_type:
            src_typed = _fetch_typed(cur, source_table, src) if source_table else None
            tgt_typed = _fetch_typed(cur, table, tgt) if table else None
            if source_table and src_typed is None:
                raise EntityMergeError(f"typed row missing for {source_type} source")
            if table and tgt_typed is None:
                raise EntityMergeError(f"typed row missing for {entity_type} target")

            # The event still involves this entity; only the kind of involvement
            # was wrong. Everything else type-specific dies with the source row.
            if src_typed and tgt_typed and source_type in _INVOLVED_COLUMN and entity_type in _INVOLVED_COLUMN:
                _move_between_array_columns(
                    cur, "events",
                    _INVOLVED_COLUMN[source_type], _INVOLVED_COLUMN[entity_type],
                    str(src_typed["id"]), str(tgt_typed["id"]),
                )
            elif src_typed and source_type in _INVOLVED_COLUMN:
                # Target is a custom type with no involved_* column of its own.
                cur.execute(
                    f"UPDATE events SET {_INVOLVED_COLUMN[source_type]} = "
                    f"array_remove({_INVOLVED_COLUMN[source_type]}, %s::uuid)",
                    (str(src_typed["id"]),),
                )

            if src_typed:
                _detach_typed_references(cur, source_type, str(src_typed["id"]))

            absorbed = [*(source.get("aliases") or []), str(source["name"])]
            if tgt_typed:
                cur.execute(
                    f"UPDATE {table} SET aliases = %s WHERE id = %s",
                    (_union_aliases(tgt_typed["name"], tgt_typed.get("aliases"), absorbed),
                     str(tgt_typed["id"])),
                )
            cur.execute(
                "UPDATE entities SET aliases = %s WHERE id = %s",
                (_union_aliases(target["name"], target.get("aliases"), absorbed), tgt),
            )
            if src_typed:
                cur.execute(f"DELETE FROM {source_table} WHERE id = %s", (str(src_typed["id"]),))

        elif table is not None:
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
                # state_deltas.location_id references locations(id) (the typed
                # row), not entities(id) — repoint with the typed ids, same
                # CASCADE-before-delete reasoning as the subject/object repoint above.
                cur.execute("UPDATE state_deltas SET location_id = %s WHERE location_id = %s", (tt, st))
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

    if cross_type:
        logger.info(
            "merged entity %s (%s) into %s (%s) — cross-type reclassification",
            src, source_type, tgt, entity_type,
        )
    else:
        logger.info("merged entity %s into %s (%s)", src, tgt, entity_type)
    return {
        "source_entity_id": src,
        "target_entity_id": tgt,
        "entity_type": entity_type,
        "source_entity_type": source_type,
        "cross_type": cross_type,
        "absorbed_name": str(source["name"]),
    }


__all__ = ["merge_entities", "EntityMergeError"]
