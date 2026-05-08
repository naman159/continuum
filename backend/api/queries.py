from __future__ import annotations

from typing import Any
from uuid import UUID

from pipeline.db.client import DBClient


_db: DBClient | None = None


def _get_db() -> DBClient:
    """DB factory; tests patch this to return a FakeDB."""
    global _db
    if _db is None:
        _db = DBClient()
    return _db


def list_novels() -> list[dict[str, Any]]:
    db = _get_db()
    novels = db.novels if hasattr(db, "novels") else _list_novels_real(db)
    chapters = db.chapters if hasattr(db, "chapters") else None

    rows: list[dict[str, Any]] = []
    for novel in novels:
        novel_id = novel["id"]
        if chapters is not None:
            max_chapter = max(
                (c["number"] for c in chapters if c["novel_id"] == novel_id),
                default=0,
            )
        else:
            max_chapter = _max_chapter_real(db, novel_id)
        rows.append(
            {
                "id": novel_id,
                "title": novel["title"],
                "author": novel.get("author"),
                "language": novel.get("language"),
                "created_at": novel["created_at"],
                "max_chapter": max_chapter,
            }
        )
    return rows


def get_novel(novel_id: UUID) -> dict[str, Any] | None:
    db = _get_db()

    if hasattr(db, "novels"):
        novel = next((n for n in db.novels if n["id"] == novel_id), None)
    else:
        novel = _get_novel_real(db, novel_id)
    if novel is None:
        return None

    if hasattr(db, "chapters"):
        max_chapter = max(
            (c["number"] for c in db.chapters if c["novel_id"] == novel_id),
            default=0,
        )
    else:
        max_chapter = _max_chapter_real(db, novel_id)

    return {
        "id": novel_id,
        "title": novel["title"],
        "author": novel.get("author"),
        "language": novel.get("language"),
        "created_at": novel["created_at"],
        "max_chapter": max_chapter,
    }


def _get_novel_real(db: DBClient, novel_id: UUID) -> dict[str, Any] | None:
    row = db.fetchone(
        """
        SELECT id, title, author, language, created_at
        FROM novels
        WHERE id = %s
        """,
        (str(novel_id),),
        dict_rows=True,
    )
    return dict(row) if row else None


def _list_novels_real(db: DBClient) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT id, title, author, language, created_at
        FROM novels
        ORDER BY created_at DESC
        """,
        dict_rows=True,
    )
    return [dict(r) for r in rows]


def _max_chapter_real(db: DBClient, novel_id: UUID) -> int:
    value = db.fetchval(
        "SELECT COALESCE(MAX(number), 0) FROM chapters WHERE novel_id = %s",
        (str(novel_id),),
    )
    return int(value or 0)
