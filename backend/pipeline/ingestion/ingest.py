from __future__ import annotations

import json
from typing import Any

from pipeline.db.client import DBClient


def ingest_chapter(
    db: DBClient,
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    title: str | None = None,
    source: str = "human",
    generation_meta: dict[str, Any] | None = None,
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
        INSERT INTO chapters (novel_id, number, title, raw_text, source, generation_meta)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
        RETURNING id
        """,
        (
            novel_id,
            chapter_number,
            title,
            raw_text,
            source,
            json.dumps(generation_meta) if generation_meta is not None else None,
        ),
        commit=True,
    )
    return str(chapter_id)


def delete_chapter_data(db: DBClient, *, novel_id: str, chapter_number: int) -> None:
    """Delete one chapter and every derived row, enabling re-processing.

    The chapters FK cascades cover events (and thread_events), scenes,
    character_states, continuity_flags, shared_dynamics, and relationships
    rows with a non-NULL chapter_id. Tables keyed by chapter *number* instead
    of a FK need explicit handling. Known non-undoable residue: plot_threads
    upserts and entity rows created by this chapter remain — re-processing
    resolves back onto them.

    located_in_edges/possesses_edges evidence pointers into this chapter are
    NULLed (their rows are projections; materialize_state rebuilds them).
    knows_edges.source_event_id and commitments.foreshadow/payoff_event_id also
    reference events with NO ACTION but are never populated by the pipeline
    today — revisit here if that changes.
    """
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
