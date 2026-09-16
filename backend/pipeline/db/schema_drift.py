"""Detect databases whose shape has drifted from schema.sql.

`CREATE TABLE IF NOT EXISTS` is a no-op once a table exists, so editing a
CREATE TABLE body -- tightening a column to NOT NULL, adding a UNIQUE or CHECK,
putting ON DELETE CASCADE on a foreign key -- changes what a *new* database
gets and nothing at all about an existing one. Columns are the exception,
because the file backfills those with explicit ADD COLUMN IF NOT EXISTS.

This project's answer to schema change is to drop and re-init rather than
migrate (see backend/README.md), which works only if you can tell that a
database needs it. That is what this module is for: apply schema.sql into a
throwaway schema, snapshot both catalogs, and diff.
"""

from __future__ import annotations

import uuid

from pipeline.db.client import DBClient

_CATALOG_SQL = """
    SELECT 'column ' || c.relname || '.' || a.attname
           || ' ' || format_type(a.atttypid, a.atttypmod)
           || CASE WHEN a.attnotnull THEN ' NOT NULL' ELSE '' END
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
     WHERE c.relnamespace = %(ns)s::text::regnamespace
       AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
    UNION ALL
    SELECT 'constraint ' || conrelid::regclass::text || ' ' || pg_get_constraintdef(oid)
      FROM pg_constraint WHERE connamespace = %(ns)s::text::regnamespace
    UNION ALL
    SELECT 'index ' || replace(
        replace(indexdef, 'INDEX ' || quote_ident(indexname) || ' ON ', 'INDEX ON '),
        %(ns)s::text, 'public'
    )
      FROM pg_indexes WHERE schemaname = %(ns)s::text
"""


def _catalog(db: DBClient, namespace: str) -> set[str]:
    rows = db.fetchall(_CATALOG_SQL, {"ns": namespace})
    # Two normalizations, both about noise rather than shape:
    #
    # Schema qualification is inconsistent -- regclass and pg_get_constraintdef
    # print a schema prefix only when the schema is outside the current
    # search_path, so the same table reads as "chapters" in one snapshot and
    # "probe_ab12.chapters" in the other. Strip the prefix from both sides.
    #
    # Constraint and index *names* are noise too: the same UNIQUE reached one
    # database as an inline table constraint and another as a named ALTER, so
    # comparing names would report that identity as drift. Compare definitions.
    out: set[str] = set()
    for (line,) in rows:
        out.add(line.replace(f"{namespace}.", "").replace("public.", ""))
    return out


def schema_drift(schema_path: str | None = None) -> list[str]:
    """Return human-readable drift lines; empty when the database matches."""
    from pipeline.pipeline import _schema_sql  # local import: avoids a cycle

    probe = "schema_probe_" + uuid.uuid4().hex[:12]
    sql = _schema_sql(schema_path)

    with DBClient() as db:
        live = _catalog(db, "public")
        try:
            with db.cursor(commit=True) as cur:
                cur.execute(f'CREATE SCHEMA "{probe}"')
                # Extensions are database-scoped and already installed; the
                # probe only needs its own tables.
                cur.execute(f'SET LOCAL search_path = "{probe}", public')
                cur.execute(sql)
            expected = _catalog(db, probe)
        finally:
            with db.cursor(commit=True) as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{probe}" CASCADE')

    return sorted(
        [f"missing:  {x}" for x in expected - live]
        + [f"unexpected: {x}" for x in live - expected]
    )


def format_drift(drift: list[str]) -> str:
    return (
        f"Database shape has drifted from schema.sql in {len(drift)} place(s):\n  "
        + "\n  ".join(drift)
        + "\n\nCREATE TABLE IF NOT EXISTS cannot retrofit constraints, NOT NULL, "
        "or ON DELETE CASCADE onto an existing table, so re-running init-db will "
        "not close these. This project drops and re-inits rather than migrating: "
        "chapters.raw_text is ground truth and every projection rebuilds from it."
    )
