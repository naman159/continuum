"""init_db applies the consolidated schema idempotently and stamps a version.

Hits the real branch-isolated Postgres. Running init_db twice must succeed
(idempotent DDL) and leave exactly one schema_version row at SCHEMA_VERSION.
The removed legacy surface (temporal_constraints, events SVO columns,
knows_edges.fact_id, chapters.generation_meta) must be gone afterward.
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
