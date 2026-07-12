"""reads.novels: cutoff-free novel listing/detail against real Postgres."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from reads.common import max_chapter_for


def list_novels(db: Any) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT id, title, author, language, created_at
        FROM novels
        ORDER BY created_at DESC
        """,
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "author": r.get("author"),
            "language": r.get("language"),
            "created_at": r["created_at"],
            "max_chapter": max_chapter_for(db, r["id"]),
        }
        for r in rows
    ]


def get_novel(db: Any, novel_id: UUID | str) -> dict[str, Any] | None:
    row = db.fetchone(
        """
        SELECT id, title, author, language, created_at
        FROM novels
        WHERE id = %s
        """,
        (novel_id,),
        dict_rows=True,
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "author": row.get("author"),
        "language": row.get("language"),
        "created_at": row["created_at"],
        "max_chapter": max_chapter_for(db, novel_id),
    }
