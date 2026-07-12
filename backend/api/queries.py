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


def _max_chapter_real(db: DBClient, novel_id: UUID) -> int:
    value = db.fetchval(
        "SELECT COALESCE(MAX(number), 0) FROM chapters WHERE novel_id = %s",
        (str(novel_id),),
    )
    return int(value or 0)


def _max_chapter_for(db: Any, novel_id: UUID) -> int:
    if hasattr(db, "chapters"):
        return max((c["number"] for c in db.chapters if c["novel_id"] == novel_id), default=0)
    return _max_chapter_real(db, novel_id)


def _resolve_cap(db: Any, novel_id: UUID, cap: int | None) -> int:
    if cap is None:
        return _max_chapter_for(db, novel_id)
    return cap


def list_chapters(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "chapters"):
        rows = [
            c for c in db.chapters
            if c["novel_id"] == novel_id and c["number"] <= effective_cap
        ]
    else:
        rows = [
            dict(r) for r in db.fetchall(
                "SELECT id, number, title, summary, summary_short, summary_long, processed_at "
                "FROM chapters WHERE novel_id = %s AND number <= %s ORDER BY number",
                (str(novel_id), effective_cap),
                dict_rows=True,
            )
        ]
    rows.sort(key=lambda r: r["number"])
    return [
        {
            "id": r["id"],
            "number": r["number"],
            "title": r.get("title"),
            "summary": r.get("summary"),
            "summary_short": r.get("summary_short"),
            "summary_long": r.get("summary_long"),
            "processed_at": r.get("processed_at"),
        }
        for r in rows
    ]

