"""reads.chapters: cutoff-aware chapter and scene reads against real Postgres."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pipeline.db.history import metadata_table

from reads.common import resolve_cutoff


def list_chapters(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    rows = [
        dict(r)
        for r in db.fetchall(
            "SELECT id, number, title, summary, summary_short, summary_long, processed_at "
            "FROM chapters WHERE novel_id = %s AND number <= %s ORDER BY number",
            (novel_id, cutoff),
            dict_rows=True,
        )
    ]

    critique_by_chapter: dict[str, dict] = {
        str(r["chapter_id"]): {
            "passed": r["passed"],
            "fails": int(r["fails"]),
            "warns": int(r["warns"]),
        }
        for r in db.fetchall(
            """
            SELECT cr.chapter_id, cr.passed,
                   count(*) FILTER (WHERE f.severity = 'fail') AS fails,
                   count(*) FILTER (WHERE f.severity = 'warn') AS warns
              FROM critique_reports cr
              LEFT JOIN critique_findings f ON f.report_id = cr.id
              JOIN chapters c ON c.id = cr.chapter_id
             WHERE c.novel_id = %s AND c.number <= %s
             GROUP BY cr.chapter_id, cr.passed
            """,
            (novel_id, cutoff),
            dict_rows=True,
        )
    }

    return [
        {
            "id": r["id"],
            "number": r["number"],
            "title": r.get("title"),
            "summary": r.get("summary"),
            "summary_short": r.get("summary_short"),
            "summary_long": r.get("summary_long"),
            "processed_at": r.get("processed_at"),
            "critique": critique_by_chapter.get(str(r["id"])),
        }
        for r in rows
    ]


def list_scenes(
    db: Any, novel_id: UUID | str, up_to_chapter: int | None, chapter: int | None
) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    where = ["ch.novel_id = %s", "ch.number <= %s"]
    params: list[Any] = [novel_id, cutoff]
    if chapter is not None:
        where.append("ch.number = %s")
        params.append(chapter)
    rows = db.fetchall(
        f"""
        SELECT s.id, s.chapter_id, ch.number AS chapter_number, s.scene_index,
               s.pov_character_id, pov.name AS pov_character_name,
               s.location_id, loc.name AS location_name,
               s.time_anchor, s.story_time_ordinal, s.summary,
               s.present_characters
          FROM scenes s
          JOIN chapters ch ON ch.id = s.chapter_id
          LEFT JOIN {metadata_table('characters', novel_id, cutoff)} pov ON pov.id = s.pov_character_id
          LEFT JOIN {metadata_table('locations', novel_id, cutoff)} loc ON loc.id = s.location_id
         WHERE {' AND '.join(where)}
         ORDER BY ch.number, s.scene_index
        """,
        tuple(params),
        dict_rows=True,
    )
    if not rows:
        return []
    # Resolve present_characters UUID[] -> names via a single batch.
    all_char_ids = sorted({str(cid) for r in rows for cid in (r["present_characters"] or [])})
    name_by_id: dict[str, str] = {}
    if all_char_ids:
        chars = db.fetchall(
            f"SELECT id, name FROM {metadata_table('characters', novel_id, cutoff)} characters WHERE id = ANY(%s::uuid[])",
            (all_char_ids,),
            dict_rows=True,
        )
        name_by_id = {str(c["id"]): c["name"] for c in chars}
    return [
        {
            "id": r["id"],
            "chapter_id": r["chapter_id"],
            "chapter_number": r["chapter_number"],
            "scene_index": r["scene_index"],
            "pov_character_id": r["pov_character_id"],
            "pov_character_name": r["pov_character_name"],
            "location_id": r["location_id"],
            "location_name": r["location_name"],
            "time_anchor": r["time_anchor"],
            "story_time_ordinal": r["story_time_ordinal"],
            "summary": r["summary"],
            "present_character_names": [
                name_by_id.get(str(cid), str(cid))
                for cid in (r["present_characters"] or [])
            ],
        }
        for r in rows
    ]
