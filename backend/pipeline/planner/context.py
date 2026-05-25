"""Gather the structured context the Scene Planner needs.

The planner must read recent state (canon facts, locked facts, active plot
threads, pending commitments, last chapter's events and key characters) and
pass it to the LLM in a compact, structured form. This module produces that
PlanContext from the database; the planner itself is independent of how the
context was built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline.db.client import DBClient


@dataclass
class PlanContext:
    novel_id: str
    chapter_number: int
    novel_title: str | None
    locked_facts: list[dict[str, Any]] = field(default_factory=list)
    pending_commitments: list[dict[str, Any]] = field(default_factory=list)
    active_threads: list[dict[str, Any]] = field(default_factory=list)
    recent_chapter_summaries: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    main_characters: list[dict[str, Any]] = field(default_factory=list)


def gather_plan_context(
    db: DBClient,
    novel_id: str,
    chapter_number: int,
    *,
    recent_chapters: int = 3,
    recent_events_limit: int = 25,
    max_characters: int = 8,
) -> PlanContext:
    novel = db.fetchone(
        "SELECT title FROM novels WHERE id = %s",
        (novel_id,),
        dict_rows=True,
    )

    locked_facts = db.fetchall(
        """
        SELECT subject_entity_id, predicate, value, source_chapter
          FROM canon_facts
         WHERE novel_id = %s AND locked = true
         ORDER BY source_chapter NULLS LAST
        """,
        (novel_id,),
        dict_rows=True,
    )

    pending_commitments = db.fetchall(
        """
        SELECT id, foreshadow_text, foreshadow_chapter, trigger_predicate, weight
          FROM commitments
         WHERE novel_id = %s AND status = 'pending'
         ORDER BY foreshadow_chapter
        """,
        (novel_id,),
        dict_rows=True,
    )

    active_threads = db.fetchall(
        """
        SELECT id, title, description, status, opened_chapter, thread_type
          FROM plot_threads
         WHERE novel_id = %s AND status IN ('open', 'progressing')
         ORDER BY opened_chapter NULLS LAST
        """,
        (novel_id,),
        dict_rows=True,
    )

    recent_chapter_summaries = db.fetchall(
        """
        SELECT number, title, summary, summary_short, summary_long
          FROM chapters
         WHERE novel_id = %s AND number < %s
         ORDER BY number DESC
         LIMIT %s
        """,
        (novel_id, chapter_number, recent_chapters),
        dict_rows=True,
    )

    recent_events = db.fetchall(
        """
        SELECT e.id, e.description, e.event_type, e.impact_level, ch.number AS chapter_number
          FROM events e
          JOIN chapters ch ON ch.id = e.chapter_id
         WHERE ch.novel_id = %s AND ch.number < %s
         ORDER BY ch.number DESC, e.created_at DESC
         LIMIT %s
        """,
        (novel_id, chapter_number, recent_events_limit),
        dict_rows=True,
    )

    # Main characters = those appearing in the most recent character_states rows.
    main_characters = db.fetchall(
        """
        SELECT DISTINCT c.id, c.name, c.aliases, c.description
          FROM characters c
          JOIN character_states cs ON cs.character_id = c.id
          JOIN chapters ch ON ch.id = cs.chapter_id
         WHERE c.novel_id = %s AND ch.number < %s
         ORDER BY c.name
         LIMIT %s
        """,
        (novel_id, chapter_number, max_characters),
        dict_rows=True,
    )

    return PlanContext(
        novel_id=novel_id,
        chapter_number=chapter_number,
        novel_title=novel["title"] if novel else None,
        locked_facts=[dict(r) for r in locked_facts],
        pending_commitments=[dict(r) for r in pending_commitments],
        active_threads=[dict(r) for r in active_threads],
        recent_chapter_summaries=[dict(r) for r in recent_chapter_summaries],
        recent_events=[dict(r) for r in recent_events],
        main_characters=[dict(r) for r in main_characters],
    )
