"""Novel admin operations (create/delete): SQL-only, real Postgres."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pipeline.db.client import DBClient

from reads.db import get_db


def create_novel(
    title: str,
    author: str | None,
    language: str | None,
    custom_entity_types: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    db = get_db()
    return _create_novel_real(db, title, author, language, custom_entity_types or [])


def _create_novel_real(
    db: DBClient,
    title: str,
    author: str | None,
    language: str | None,
    custom_entity_types: list[dict[str, Any]],
) -> dict[str, Any]:
    row = db.fetchone(
        """
        INSERT INTO novels (id, title, author, language, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        RETURNING id, title, author, language, created_at
        """,
        (str(uuid4()), title, author, language),
        dict_rows=True,
        commit=True,
    )
    novel_id = str(row["id"])
    for et in custom_entity_types:
        db.execute(
            """
            INSERT INTO novel_entity_types (novel_id, name, description)
            VALUES (%s, %s, %s)
            ON CONFLICT (novel_id, name) DO NOTHING
            """,
            (novel_id, et["name"], et.get("description")),
        )
    return {**dict(row), "max_chapter": 0}


def delete_novel(novel_id: UUID) -> bool:
    db = get_db()
    return _delete_novel_real(db, novel_id)


def _delete_novel_real(db: DBClient, novel_id: UUID) -> bool:
    row = db.fetchone(
        "DELETE FROM novels WHERE id = %s RETURNING id",
        (str(novel_id),),
        commit=True,
    )
    return row is not None


# --------------------------------------------------------------------------
# Canon fact mutations (moved from api/queries.py). Reads live in
# reads.knowledge.list_canon_facts; these are the write-side counterparts.
# --------------------------------------------------------------------------


def update_canon_fact(
    novel_id: UUID, fact_id: UUID, *, locked: bool | None, value: str | None
) -> bool:
    """Patch a canon fact's lock state and/or value in one atomic statement.

    value edits reset confidence to 1.0 (manual entry is authoritative).
    Returns False when the fact doesn't exist in this novel.
    """
    db = get_db()
    existing = db.fetchone(
        "SELECT id FROM canon_facts WHERE id = %s AND novel_id = %s",
        (str(fact_id), str(novel_id)),
        dict_rows=True,
    )
    if existing is None:
        return False
    db.execute(
        """
        UPDATE canon_facts
           SET locked = COALESCE(%s, locked),
               value = COALESCE(%s, value),
               confidence = CASE WHEN %s::text IS NULL THEN confidence ELSE 1.0 END
         WHERE id = %s AND novel_id = %s
        """,
        (locked, value, value, str(fact_id), str(novel_id)),
    )
    return True


def create_canon_fact(
    novel_id: UUID,
    *,
    subject_entity_id: UUID,
    predicate: str,
    value: str,
    kind: str = "other",
    locked: bool = False,
) -> dict[str, Any] | None:
    """Upsert a canon fact; on conflict OVERWRITES value/locked/confidence.

    Manual entry is authoritative — unlike the pipeline's ON CONFLICT DO
    NOTHING, an admin create deliberately replaces what extraction stored.
    """
    db = get_db()
    row = db.fetchone(
        """
        INSERT INTO canon_facts (novel_id, kind, subject_entity_id, predicate, value, confidence, locked)
        VALUES (%s, %s, %s, %s, %s, 1.0, %s)
        ON CONFLICT (novel_id, subject_entity_id, predicate)
        DO UPDATE SET value = EXCLUDED.value, locked = EXCLUDED.locked, confidence = 1.0
        RETURNING id, kind, subject_entity_id, predicate, value, source_chapter, confidence, locked
        """,
        (str(novel_id), kind, str(subject_entity_id), predicate.strip().lower(), value, locked),
        dict_rows=True,
        commit=True,
    )
    return dict(row) if row else None


def delete_canon_fact(novel_id: UUID, fact_id: UUID) -> bool:
    db = get_db()
    existing = db.fetchone(
        "SELECT id FROM canon_facts WHERE id = %s AND novel_id = %s",
        (str(fact_id), str(novel_id)),
        dict_rows=True,
    )
    if existing is None:
        return False
    db.execute(
        "DELETE FROM canon_facts WHERE id = %s AND novel_id = %s",
        (str(fact_id), str(novel_id)),
    )
    return True
