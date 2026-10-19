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


def test_upgrade_preserves_legacy_knowledge_and_rebuilds_snapshots(db, seed_novel):
    seeded = seed_novel(db)
    nid = seeded['novel_id']
    row = db.fetchone('SELECT id,entity_id FROM characters WHERE novel_id=%s ORDER BY name LIMIT 1', (nid,))
    cid, eid = row
    chapter_id = db.fetchval('SELECT id FROM chapters WHERE novel_id=%s AND number=1', (nid,))
    db.execute('ALTER TABLE state_deltas DROP CONSTRAINT state_deltas_kind_check')
    try:
        db.execute("INSERT INTO state_deltas(chapter_id,kind,subject_id,detail) VALUES(%s,'knowledge',%s,'Preserve the old fact.')", (chapter_id, eid))
        db.execute("INSERT INTO knows_edges(character_id,fact_description,learned_chapter,source_type) VALUES(%s,'Keep the enriched fact.',1,'observation')", (cid,))
        init_db()
        init_db()
        facts = {r[0] for r in db.fetchall('SELECT fact_description FROM knows_edges WHERE character_id=%s', (cid,))}
        snapshot = db.fetchval('SELECT knowledge FROM character_states s JOIN chapters c ON c.id=s.chapter_id WHERE s.character_id=%s ORDER BY c.number DESC LIMIT 1', (cid,))
        assert {'Preserve the old fact.', 'Keep the enriched fact.'} <= facts
        assert set(snapshot) == facts
        assert db.fetchval("SELECT count(*) FROM knows_edges WHERE character_id=%s AND fact_description='Preserve the old fact.'", (cid,)) == 1
    finally:
        init_db()


def test_story_record_rename_preserves_versions_tombstones_and_baseline(db):
    import json
    import uuid
    from pipeline.pipeline import _schema_sql
    ns = 'history_upgrade_' + uuid.uuid4().hex[:10]
    sql = _schema_sql()
    with db.transaction() as cur:
        cur.execute(f'CREATE SCHEMA {ns}')
        cur.execute(f'SET LOCAL search_path = {ns}, public')
        cur.execute(sql)
        cur.execute("INSERT INTO novels(title) VALUES ('Legacy') RETURNING id")
        nid = cur.fetchone()[0]
        rid = uuid.uuid4()
        cur.execute('INSERT INTO story_record_history VALUES (%s, 0)', (nid,))
        cur.execute("INSERT INTO story_record_versions VALUES (%s,'characters',%s,1,%s::jsonb),(%s,'characters',%s,2,NULL)",
                    (nid, rid, json.dumps({'id': str(rid), 'name': 'Preserved'}), nid, rid))
        cur.execute('ALTER TABLE story_record_history RENAME TO metadata_history')
        cur.execute('ALTER TABLE story_record_versions RENAME TO metadata_versions')
        cur.execute('ALTER FUNCTION story_records_at(text, uuid, integer) RENAME TO metadata_at')
        cur.execute(sql)
        cur.execute(sql)  # startup upgrade is repeatable
        cur.execute('SELECT baseline_chapter FROM story_record_history WHERE novel_id=%s', (nid,))
        assert cur.fetchone()[0] == 0
        cur.execute('SELECT chapter_number, payload FROM story_record_versions WHERE novel_id=%s ORDER BY chapter_number', (nid,))
        assert cur.fetchall() == [(1, {'id': str(rid), 'name': 'Preserved'}), (2, None)]
        cur.execute("SELECT payload FROM story_records_at('characters',%s,1)", (nid,))
        assert cur.fetchone()[0]['name'] == 'Preserved'
        cur.execute("SELECT count(*) FROM story_records_at('characters',%s,2)", (nid,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT to_regclass(%s), to_regprocedure(%s)", (f'{ns}.metadata_versions', f'{ns}.metadata_at(text,uuid,integer)'))
        assert cur.fetchone() == (None, None)
        cur.execute(f'DROP SCHEMA {ns} CASCADE')
