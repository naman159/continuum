"""init_db applies the consolidated schema idempotently and stamps a version.

Hits the real branch-isolated Postgres. Running init_db twice must succeed
(idempotent DDL) and leave exactly one schema_version row at SCHEMA_VERSION.
The removed legacy surface (temporal_constraints, events SVO columns,
knows_edges.fact_id, chapters.generation_meta, the entity_type CHECK) must be
gone afterward.
"""

from __future__ import annotations

from pipeline.db.client import DBClient
from pipeline.pipeline import SCHEMA_VERSION, init_db


def _column_exists(db: DBClient, table: str, column: str) -> bool:
    return bool(
        db.fetchval(
            """
            SELECT 1 FROM information_schema.columns
             WHERE table_name = %s AND column_name = %s
            """,
            (table, column),
        )
    )


def _check_constraint_exists(db: DBClient, table: str, constraint: str) -> bool:
    return bool(
        db.fetchval(
            """
            SELECT 1 FROM pg_constraint
             WHERE conrelid = %s::regclass AND contype = 'c' AND conname = %s
            """,
            (table, constraint),
        )
    )


def _table_exists(db: DBClient, table: str) -> bool:
    return bool(
        db.fetchval(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table,),
        )
    )


def test_init_db_is_idempotent_and_stamps_version():
    init_db()
    init_db()  # second run must not raise
    with DBClient() as db:
        rows = db.fetchall("SELECT version FROM schema_version")
        assert len(rows) == 1
        assert rows[0][0] == SCHEMA_VERSION


def test_new_spine_tables_exist_and_legacy_surface_is_gone():
    init_db()
    with DBClient() as db:
        assert _table_exists(db, "state_deltas")
        assert _table_exists(db, "critique_reports")
        assert _table_exists(db, "critique_findings")
        assert not _table_exists(db, "temporal_constraints")
        for col in ("subject_entity_id", "verb", "object_entity_id",
                    "story_time_ordinal", "narrative_order", "scene_id"):
            assert not _column_exists(db, "events", col), col
        assert not _column_exists(db, "knows_edges", "fact_id")
        assert not _column_exists(db, "chapters", "generation_meta")


def test_init_db_leaves_no_drift_from_schema_sql():
    # Applying schema.sql is not the same as conforming to it: CREATE TABLE IF
    # NOT EXISTS silently skips a table that already exists, so a constraint,
    # a NOT NULL, or an ON DELETE CASCADE added to a table body never reaches a
    # database that predates it. This catches the whole class at once.
    from pipeline.db.schema_drift import format_drift, schema_drift

    init_db()
    drift = schema_drift()
    assert drift == [], format_drift(drift)


def test_entity_type_check_constraint_is_dropped_on_existing_databases():
    # Relaxing the constraint in the CREATE TABLE body only reached fresh
    # databases -- CREATE TABLE IF NOT EXISTS is a no-op once the table is
    # there -- so every pre-existing DB kept rejecting custom entity types.
    # Constraints need their own explicit ALTER, like columns do.
    init_db()
    with DBClient() as db:
        assert not _check_constraint_exists(db, "entities", "entities_entity_type_check")


def test_schema_drift_ignores_equivalent_constraint_and_index_names():
    from pipeline.db.schema_drift import schema_drift

    with DBClient() as db:
        db.execute(
            'ALTER TABLE chapters RENAME CONSTRAINT chapters_novel_id_number_key '
            'TO "renamed chapter unique"'
        )
        try:
            assert schema_drift() == []
            # Still detect a real shape change, not just differences in names.
            db.execute("ALTER TABLE chapters ALTER COLUMN raw_text DROP NOT NULL")
            try:
                assert any("column chapters.raw_text" in line for line in schema_drift())
            finally:
                db.execute("ALTER TABLE chapters ALTER COLUMN raw_text SET NOT NULL")
        finally:
            db.execute(
                'ALTER TABLE chapters RENAME CONSTRAINT "renamed chapter unique" '
                'TO chapters_novel_id_number_key'
            )
