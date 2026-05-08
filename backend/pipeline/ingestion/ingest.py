from __future__ import annotations

from pipeline.db.client import DBClient


def ingest_chapter(
    db: DBClient,
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    title: str | None = None,
) -> str:
    existing = db.fetchval(
        """
        SELECT id FROM chapters
        WHERE novel_id = %s AND number = %s
        """,
        (novel_id, chapter_number),
    )
    if existing is not None:
        raise ValueError(
            f"Chapter {chapter_number} already exists for novel {novel_id}. "
            "Re-processing is not supported."
        )

    chapter_id = db.fetchval(
        """
        INSERT INTO chapters (novel_id, number, title, raw_text)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (novel_id, chapter_number, title, raw_text),
        commit=True,
    )
    return str(chapter_id)
