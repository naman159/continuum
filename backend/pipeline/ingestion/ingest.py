from __future__ import annotations

from pipeline.db.client import DBClient


def ingest_chapter(
    db: DBClient,
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    title: str | None = None,
    source: str = "human",
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
            "Use replace=True to re-process it."
        )

    chapter_id = db.fetchval(
        """
        INSERT INTO chapters (novel_id, number, title, raw_text, source)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (novel_id, chapter_number, title, raw_text, source),
        commit=True,
    )
    return str(chapter_id)


def delete_chapter_data(db: DBClient, *, novel_id: str, chapter_number: int) -> None:
    """Delete chapter-owned rows during the coordinator's transactional rewind.

    FK cascades cover events, scenes, states, flags, dynamics and relationships.
    Number-keyed assertions need explicit deletion. The coordinator then restores
    versioned entity/canon/thread metadata and rebuilds all dependent chapters.
    """
    # A suffix rewind restores older beliefs when their replacements vanish.
    # Clear incoming supersession links in the same transaction before deleting
    # number-keyed assertions (their self-FK otherwise prevents the rewind).
    db.execute(
        """
        UPDATE knows_edges SET superseded_by_id = NULL
         WHERE superseded_by_id IN (
             SELECT k.id FROM knows_edges k JOIN characters c ON c.id = k.character_id
              WHERE c.novel_id = %s AND k.learned_chapter = %s
         )
        """,
        (novel_id, chapter_number),
    )
    # knows_edges has no chapter FK — keyed by learned_chapter int.
    db.execute(
        """
        DELETE FROM knows_edges
         WHERE learned_chapter = %s
           AND character_id IN (SELECT id FROM characters WHERE novel_id = %s)
        """,
        (chapter_number, novel_id),
    )
    # Payoffs this chapter satisfied go back to pending.
    db.execute(
        """
        UPDATE commitments
           SET status = 'pending', payoff_chapter = NULL, payoff_text = NULL,
               updated_at = now()
         WHERE novel_id = %s AND payoff_chapter = %s
        """,
        (novel_id, chapter_number),
    )
    # Foreshadows this chapter introduced disappear with it.
    db.execute(
        "DELETE FROM commitments WHERE novel_id = %s AND foreshadow_chapter = %s",
        (novel_id, chapter_number),
    )
    # located_in_edges / possesses_edges reference events with NO ACTION FKs;
    # NULL their evidence pointers so the chapter's event cascade can't violate
    # them. The rows themselves are materialized projections — re-running the
    # state materializer rebuilds them from the surviving event log.
    db.execute(
        """
        UPDATE located_in_edges SET evidence_event_id = NULL
         WHERE evidence_event_id IN (
           SELECT e.id FROM events e
             JOIN chapters ch ON ch.id = e.chapter_id
            WHERE ch.novel_id = %s AND ch.number = %s
         )
        """,
        (novel_id, chapter_number),
    )
    db.execute(
        """
        UPDATE possesses_edges SET evidence_event_id = NULL
         WHERE evidence_event_id IN (
           SELECT e.id FROM events e
             JOIN chapters ch ON ch.id = e.chapter_id
            WHERE ch.novel_id = %s AND ch.number = %s
         )
        """,
        (novel_id, chapter_number),
    )
    # Relationships rows written before chapter_id existed (NULL) cannot be
    # attributed; rows written since cascade with the chapter.
    db.execute(
        "DELETE FROM chapters WHERE novel_id = %s AND number = %s",
        (novel_id, chapter_number),
    )
