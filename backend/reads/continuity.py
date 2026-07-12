"""reads.continuity: cutoff-aware continuity flags and critique reads."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from reads.common import resolve_cutoff


def list_flags(
    db: Any, novel_id: UUID | str, up_to_chapter: int | None, resolved_filter: str
) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    raw = db.fetchall(
        """
        SELECT cf.id, cf.description, cf.flag_type, cf.resolved, cf.resolved_chapter_id,
               ch.number AS chapter_number
        FROM continuity_flags cf
        JOIN chapters ch ON ch.id = cf.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
        ORDER BY ch.number
        """,
        (novel_id, cutoff),
        dict_rows=True,
    )
    chap_lookup = {
        r["id"]: r["number"]
        for r in db.fetchall(
            "SELECT id, number FROM chapters WHERE novel_id = %s", (novel_id,), dict_rows=True
        )
    }
    out: list[dict[str, Any]] = []
    for r in raw:
        resolved_at_id = r.get("resolved_chapter_id")
        resolved_chapter_number = chap_lookup.get(resolved_at_id)
        effectively_resolved = bool(r.get("resolved")) and (
            resolved_chapter_number is None or resolved_chapter_number <= cutoff
        )
        if resolved_filter == "open" and effectively_resolved:
            continue
        out.append(
            {
                "id": r["id"],
                "chapter_number": r["chapter_number"],
                "description": r["description"],
                "flag_type": r.get("flag_type"),
                "resolved": effectively_resolved,
                "resolved_chapter_number": resolved_chapter_number if effectively_resolved else None,
            }
        )
    return out


def get_chapter_critique(db: Any, novel_id: UUID | str, chapter_number: int) -> dict[str, Any] | None:
    report = db.fetchone(
        """
        SELECT cr.id, cr.passed, cr.ran_at, cr.stats
          FROM critique_reports cr
          JOIN chapters c ON c.id = cr.chapter_id
         WHERE c.novel_id = %s AND c.number = %s
        """,
        (novel_id, chapter_number),
        dict_rows=True,
    )
    if report is None:
        return None
    findings = db.fetchall(
        """
        SELECT check_name, severity, message, quote, evidence
          FROM critique_findings
         WHERE report_id = %s
         ORDER BY CASE severity WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, check_name
        """,
        (report["id"],),
        dict_rows=True,
    )
    return {
        "chapter_number": chapter_number,
        "passed": report["passed"],
        "ran_at": report["ran_at"],
        "stats": report.get("stats"),
        "findings": [dict(f) for f in findings],
    }


def list_critiques(db: Any, novel_id: UUID | str, up_to_chapter: int | None) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    rows = db.fetchall(
        """
        SELECT c.number AS chapter_number, cr.passed,
               count(*) FILTER (WHERE f.severity = 'fail') AS fails,
               count(*) FILTER (WHERE f.severity = 'warn') AS warns
          FROM critique_reports cr
          JOIN chapters c ON c.id = cr.chapter_id
          LEFT JOIN critique_findings f ON f.report_id = cr.id
         WHERE c.novel_id = %s AND c.number <= %s
         GROUP BY c.number, cr.passed
         ORDER BY c.number
        """,
        (novel_id, cutoff),
        dict_rows=True,
    )
    return [
        {
            "chapter_number": r["chapter_number"],
            "passed": r["passed"],
            "fails": int(r["fails"]),
            "warns": int(r["warns"]),
        }
        for r in rows
    ]
