"""Chapter versions of mutable metadata and transactional rewind support.

Only changed rows are stored. PostgreSQL composite records keep the read model
aligned with the table schema; metadata_at supplies those records to explicit
cutoff-aware queries. These are chapter versions, not an audit of every SQL edit.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

TABLES = ("entities", "characters", "locations", "objects", "factions", "plot_threads", "canon_facts")


def metadata_table(table: str, novel_id: UUID | str, cutoff: int) -> str:
    """SQL relation for a historical metadata table; identifiers are allowlisted.

    UUID/int conversion makes the two literals safe to compose with SQL that
    already uses either positional or named parameters.
    """
    if table not in TABLES:
        raise ValueError(f"Not a versioned metadata table: {table}")
    return (
        f"(SELECT (jsonb_populate_record(NULL::public.{table}, payload)).* "
        f"FROM metadata_at('{table}', '{UUID(str(novel_id))}'::uuid, {int(cutoff)}))"
    )


def capture_metadata(db: Any, novel_id: str, chapter_number: int) -> None:
    """Record changes in the caller's chapter/admin transaction."""
    db.execute(
        "INSERT INTO metadata_history (novel_id, baseline_chapter) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (novel_id, chapter_number),
    )
    for table in TABLES:
        db.execute(
            f"""
            WITH previous AS (
                SELECT DISTINCT ON (row_id) row_id, payload
                  FROM metadata_versions
                 WHERE novel_id = %s AND table_name = %s AND chapter_number <= %s
                 ORDER BY row_id, chapter_number DESC
            ), current_rows AS (
                SELECT id AS row_id, to_jsonb(t) AS payload FROM {table} t WHERE novel_id = %s
            )
            INSERT INTO metadata_versions (novel_id, table_name, row_id, chapter_number, payload)
            SELECT %s, %s, COALESCE(c.row_id, p.row_id), %s, c.payload
              FROM current_rows c FULL JOIN previous p USING (row_id)
             WHERE c.payload IS DISTINCT FROM p.payload
            ON CONFLICT (novel_id, table_name, row_id, chapter_number)
            DO UPDATE SET payload = EXCLUDED.payload
            """,
            (novel_id, table, chapter_number, novel_id, novel_id, table, chapter_number),
        )


def restore_metadata(db: Any, novel_id: str, chapter_number: int) -> None:
    """Restore the prefix after deleting dependent chapters, in one transaction.

    Imported legacy data has no reconstructable earlier metadata. Refuse such a
    rewind rather than using future descriptions/aliases as extraction context.
    """
    baseline = db.fetchval("SELECT baseline_chapter FROM metadata_history WHERE novel_id = %s", (novel_id,))
    if baseline is None or chapter_number < baseline:
        raise ValueError(
            "This chapter predates recorded metadata history. Re-import the novel's original "
            "chapters into a new novel to establish complete history before replacing it."
        )
    # Materialized references will be rebuilt before any model sees context.
    db.execute("DELETE FROM located_in_edges WHERE entity_id IN (SELECT id FROM entities WHERE novel_id = %s)", (novel_id,))
    db.execute("DELETE FROM possesses_edges WHERE character_id IN (SELECT id FROM characters WHERE novel_id = %s)", (novel_id,))
    db.execute("UPDATE locations SET parent_location_id = NULL WHERE novel_id = %s", (novel_id,))
    for table in reversed(TABLES):
        # All remaining chapter-owned references belong to the retained prefix.
        # Their entities must also exist in that prefix's metadata versions.
        db.execute(
            f"DELETE FROM {table} WHERE novel_id = %s AND id NOT IN (SELECT id FROM {metadata_table(table, novel_id, chapter_number)} retained)",
            (novel_id,),
        )
    for table in TABLES:
        columns = [r[0] for r in db.fetchall(
            "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
            (table,),
        )]
        updates = ", ".join(f'"{column}" = EXCLUDED."{column}"' for column in columns if column != "id")
        db.execute(
            f"INSERT INTO {table} SELECT * FROM {metadata_table(table, novel_id, chapter_number)} retained "
            f"WHERE true ON CONFLICT (id) DO UPDATE SET {updates}"
        )
    db.execute("DELETE FROM metadata_versions WHERE novel_id = %s AND chapter_number > %s", (novel_id, chapter_number))


def repair_identity_history(
    db: Any, novel_id: str, source: dict, target: dict,
    source_typed: dict | None, target_typed: dict | None,
) -> None:
    """Apply an explicit identity correction to earlier metadata references too.

    Merge repairs are retroactive: old chapter assertions already point at the
    surviving identity. Keeping old metadata IDs would make historical joins
    disagree and a later rewind could resurrect the deleted duplicate.
    """
    import json
    from pipeline.entity_tables import TYPED_TABLES

    replacements = {str(source["id"]): str(target["id"])}
    source_table = TYPED_TABLES.get(source["entity_type"], "entities")
    target_table = TYPED_TABLES.get(target["entity_type"], "entities")
    if source_typed:
        replacements[str(source_typed["id"])] = str(target_typed["id"]) if target_typed else None

    def repoint(value):
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [mapped for v in value if (mapped := repoint(v)) is not None]
        if isinstance(value, dict):
            return {key: repoint(v) for key, v in value.items()}
        return value

    rows = db.fetchall(
        "SELECT table_name, row_id, chapter_number, payload FROM metadata_versions WHERE novel_id = %s ORDER BY chapter_number",
        (novel_id,), dict_rows=True,
    )
    rewritten: dict[tuple, dict | None] = {}
    # Process absorbed records first so an existing target version wins a
    # collision, while retaining both alias sets and the earliest appearance.
    rows.sort(key=lambda row: (row["chapter_number"], str(row["row_id"]) not in replacements))
    for row in rows:
        table, old_id = row["table_name"], str(row["row_id"])
        payload = repoint(row["payload"])
        row_id = replacements.get(old_id, old_id)
        if row_id is None:
            continue
        if old_id in replacements and payload is None:
            continue
        if source_typed and old_id == str(source_typed["id"]) and table == source_table:
            table = target_table
        if payload is not None and row_id in replacements.values() and table in {"entities", target_table}:
            previous_name = payload.get("name")
            payload["name"] = target["name"]
            if table == "entities":
                payload["entity_type"] = target["entity_type"]
            payload["aliases"] = list(dict.fromkeys([
                *(payload.get("aliases") or []), previous_name, source["name"],
            ]))
            payload["aliases"] = [a for a in payload["aliases"] if a and a != target["name"]]
        if source_typed is None and target_typed and old_id == str(source["id"]) and payload:
            rewritten[(target_table, str(target_typed["id"]), row["chapter_number"])] = {
                "id": str(target_typed["id"]), "entity_id": str(target["id"]),
                "novel_id": str(novel_id), "name": payload["name"],
                "aliases": payload.get("aliases", []),
                "first_appearance_chapter": row["chapter_number"],
            }
        key = (table, row_id, row["chapter_number"])
        existing = rewritten.get(key)
        if existing and payload:
            appearances = [v for v in (existing.get("first_appearance_chapter"), payload.get("first_appearance_chapter")) if v is not None]
            if appearances:
                payload["first_appearance_chapter"] = min(appearances)
            payload["aliases"] = list(dict.fromkeys([*(existing.get("aliases") or []), *(payload.get("aliases") or [])]))
        rewritten[key] = payload
    db.execute("DELETE FROM metadata_versions WHERE novel_id = %s", (novel_id,))
    for (table, row_id, chapter), payload in rewritten.items():
        db.execute(
            "INSERT INTO metadata_versions (novel_id, table_name, row_id, chapter_number, payload) VALUES (%s, %s, %s, %s, %s::jsonb)",
            (novel_id, table, row_id, chapter, json.dumps(payload) if payload is not None else None),
        )
