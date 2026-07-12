"""reads.commitments: cutoff-aware foreshadowing/payoff reads against real Postgres.

Each row keeps the raw `status` column (the novel-wide truth) and adds a
derived `status_at_cutoff`: a commitment paid off in a chapter later than
`up_to_chapter` still reads as 'pending' at that cutoff, matching a reader
who hasn't gotten there yet. `age_chapters` is computed against
`status_at_cutoff` (not the raw `status`) for the same reason.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from reads.common import resolve_cutoff


def list_commitments(
    db: Any, novel_id: UUID | str, up_to_chapter: int | None, status: str | None = None
) -> list[dict[str, Any]]:
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    rows = db.fetchall(
        """
        SELECT id, foreshadow_text, foreshadow_chapter, payoff_text, payoff_chapter,
               trigger_predicate, status, weight, related_entity_ids,
               CASE
                 WHEN status IN ('broken','abandoned') THEN status
                 WHEN payoff_chapter IS NOT NULL AND payoff_chapter <= %(cutoff)s THEN 'satisfied'
                 ELSE 'pending'
               END AS status_at_cutoff
          FROM commitments
         WHERE novel_id = %(novel_id)s AND foreshadow_chapter <= %(cutoff)s
         ORDER BY status, foreshadow_chapter
        """,
        {"novel_id": novel_id, "cutoff": cutoff},
        dict_rows=True,
    )
    if status is not None and status != "all":
        rows = [r for r in rows if r["status_at_cutoff"] == status]
    if not rows:
        return []

    # Resolve related entity names.
    all_eids = sorted({str(eid) for r in rows for eid in (r["related_entity_ids"] or [])})
    name_by_id: dict[str, str] = {}
    if all_eids:
        ents = db.fetchall(
            "SELECT id, name FROM entities WHERE id = ANY(%(ids)s::uuid[])",
            {"ids": all_eids},
            dict_rows=True,
        )
        name_by_id = {str(e["id"]): e["name"] for e in ents}
    return [
        {
            "id": r["id"],
            "foreshadow_text": r["foreshadow_text"],
            "foreshadow_chapter": r["foreshadow_chapter"],
            "payoff_text": r["payoff_text"],
            "payoff_chapter": r["payoff_chapter"],
            "trigger_predicate": r["trigger_predicate"],
            "status": r["status"],
            "status_at_cutoff": r["status_at_cutoff"],
            "weight": float(r["weight"]) if r["weight"] is not None else None,
            "related_entity_names": [
                name_by_id.get(str(eid), str(eid))
                for eid in (r["related_entity_ids"] or [])
            ],
            "age_chapters": cutoff - r["foreshadow_chapter"]
            if r["status_at_cutoff"] == "pending"
            else None,
        }
        for r in rows
    ]
