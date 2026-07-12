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
