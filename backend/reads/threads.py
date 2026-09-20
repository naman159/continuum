"""reads.threads: cutoff-aware plot-thread reads against real Postgres.

`list_threads` serves both the wiki and MCP. Status and closure fields are
masked at the cutoff, and per-thread event lists use the same chapter bound.
Thread descriptions and status come from the metadata version at that cutoff.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pipeline.db.history import metadata_table

from reads.common import resolve_cutoff_and_uncapped


def list_threads(
    db: Any, novel_id: UUID | str, up_to_chapter: int | None, status: str = "all"
) -> list[dict[str, Any]]:
    cutoff, uncapped = resolve_cutoff_and_uncapped(db, novel_id, up_to_chapter)
    rows = db.fetchall(
        f"""
        SELECT pt.id, pt.title, pt.description, pt.status, pt.thread_type,
               pt.opened_chapter, pt.closed_chapter,
               CASE
                 WHEN pt.status = 'closed'
                      AND pt.closed_chapter IS NOT NULL
                      AND pt.closed_chapter <= %(cutoff)s THEN 'closed'
                 WHEN pt.status = 'closed'
                      AND pt.closed_chapter IS NULL
                      AND %(uncapped)s THEN 'closed'
                 WHEN pt.status = 'closed' THEN 'progressing'
                 ELSE pt.status
               END AS status_at_cutoff
          FROM {metadata_table('plot_threads', novel_id, cutoff)} pt
         WHERE pt.novel_id = %(novel_id)s
           AND (pt.opened_chapter IS NULL OR pt.opened_chapter <= %(cutoff)s)
         ORDER BY pt.opened_chapter NULLS LAST, pt.title
        """,
        {"novel_id": novel_id, "cutoff": cutoff, "uncapped": uncapped},
        dict_rows=True,
    )
    if status != "all":
        rows = [r for r in rows if r["status_at_cutoff"] == status]
    if not rows:
        return []

    thread_ids = [str(r["id"]) for r in rows]
    event_rows = db.fetchall(
        """
        SELECT te.thread_id, te.event_id, te.impact, e.description,
               e.event_type, e.impact_level, ch.number AS chapter_number
          FROM thread_events te
          JOIN events e ON e.id = te.event_id
          JOIN chapters ch ON ch.id = e.chapter_id
         WHERE te.thread_id = ANY(%(thread_ids)s::uuid[]) AND ch.number <= %(cutoff)s
         ORDER BY ch.number, e.created_at
        """,
        {"thread_ids": thread_ids, "cutoff": cutoff},
        dict_rows=True,
    )
    events_by_thread: dict[str, list[dict[str, Any]]] = {}
    for r in event_rows:
        events_by_thread.setdefault(str(r["thread_id"]), []).append(
            {
                "event_id": r["event_id"],
                "description": r["description"],
                "event_type": r.get("event_type"),
                "impact_level": r.get("impact_level"),
                "chapter_number": r["chapter_number"],
                "impact": r.get("impact"),
            }
        )

    return [
        {
            "id": r["id"],
            "title": r["title"],
            "description": r.get("description"),
            "status": r["status_at_cutoff"],
            "status_at_cutoff": r["status_at_cutoff"],
            "thread_type": r.get("thread_type"),
            "opened_chapter": r.get("opened_chapter"),
            "closed_chapter": r.get("closed_chapter") if r["status_at_cutoff"] == "closed" else None,
            "events": events_by_thread.get(str(r["id"]), []),
        }
        for r in rows
    ]
