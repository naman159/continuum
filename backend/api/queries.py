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


def list_threads(novel_id: UUID, cap: int | None, status: str) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "plot_threads"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        threads: list[dict[str, Any]] = []
        for t in db.plot_threads:
            if t["novel_id"] != novel_id:
                continue
            if t.get("opened_chapter") is not None and t["opened_chapter"] > effective_cap:
                continue
            effective_status = t["status"]
            closed_chapter = t.get("closed_chapter")
            if effective_status == "closed" and closed_chapter is not None and closed_chapter > effective_cap:
                effective_status = "progressing"
                closed_chapter = None
            if status != "all" and effective_status != status:
                continue
            event_links: list[dict[str, Any]] = []
            for te in db.thread_events:
                if te["thread_id"] != t["id"]:
                    continue
                evt = next((e for e in db.events if e["id"] == te["event_id"]), None)
                if evt is None:
                    continue
                ch = chapter_by_id.get(evt["chapter_id"])
                if ch is None or ch["number"] > effective_cap:
                    continue
                event_links.append(
                    {
                        "event_id": evt["id"],
                        "description": evt["description"],
                        "chapter_number": ch["number"],
                        "impact": te.get("impact"),
                    }
                )
            threads.append(
                {
                    "id": t["id"],
                    "title": t["title"],
                    "description": t.get("description"),
                    "status": effective_status,
                    "thread_type": t.get("thread_type"),
                    "opened_chapter": t.get("opened_chapter"),
                    "closed_chapter": closed_chapter,
                    "events": event_links,
                }
            )
        return threads

    threads_raw = db.fetchall(
        """
        SELECT id, title, description, status, thread_type, opened_chapter, closed_chapter
        FROM plot_threads
        WHERE novel_id = %s AND (opened_chapter IS NULL OR opened_chapter <= %s)
        ORDER BY opened_chapter NULLS LAST, title
        """,
        (str(novel_id), effective_cap),
        dict_rows=True,
    )
    result: list[dict[str, Any]] = []
    for t in threads_raw:
        effective_status = t["status"]
        closed_chapter = t.get("closed_chapter")
        if effective_status == "closed" and closed_chapter is not None and closed_chapter > effective_cap:
            effective_status = "progressing"
            closed_chapter = None
        if status != "all" and effective_status != status:
            continue
        event_links_raw = db.fetchall(
            """
            SELECT te.event_id, te.impact, e.description, ch.number AS chapter_number
            FROM thread_events te
            JOIN events e ON e.id = te.event_id
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE te.thread_id = %s AND ch.number <= %s
            ORDER BY ch.number
            """,
            (str(t["id"]), effective_cap),
            dict_rows=True,
        )
        result.append(
            {
                "id": t["id"],
                "title": t["title"],
                "description": t.get("description"),
                "status": effective_status,
                "thread_type": t.get("thread_type"),
                "opened_chapter": t.get("opened_chapter"),
                "closed_chapter": closed_chapter,
                "events": [dict(r) for r in event_links_raw],
            }
        )
    return result


# ============================================================================
# SOTA-upgrade queries: scenes, knows, commitments, canon, location/possession.
# These use the real DB only (the in-memory fake DB used by some tests does not
# know about these tables).
# ============================================================================


def list_commitments(
    novel_id: UUID,
    cap: int | None,
    status_filter: str,
) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    where = ["novel_id = %s", "foreshadow_chapter <= %s"]
    params: list[Any] = [str(novel_id), effective_cap]
    if status_filter != "all":
        where.append("status = %s")
        params.append(status_filter)
    rows = db.fetchall(
        f"""
        SELECT id, foreshadow_text, foreshadow_chapter, payoff_text, payoff_chapter,
               trigger_predicate, status, weight, related_entity_ids
          FROM commitments
         WHERE {' AND '.join(where)}
         ORDER BY status, foreshadow_chapter
        """,
        tuple(params),
        dict_rows=True,
    )
    # Resolve related entity names.
    all_eids = sorted({str(eid) for r in rows for eid in (r["related_entity_ids"] or [])})
    name_by_id: dict[str, str] = {}
    if all_eids:
        ents = db.fetchall(
            "SELECT id, name FROM entities WHERE id = ANY(%s::uuid[])",
            (all_eids,),
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
            "weight": float(r["weight"]) if r["weight"] is not None else None,
            "related_entity_names": [
                name_by_id.get(str(eid), str(eid))
                for eid in (r["related_entity_ids"] or [])
            ],
            "age_chapters": effective_cap - r["foreshadow_chapter"]
            if r["status"] == "pending"
            else None,
        }
        for r in rows
    ]


def list_canon_facts(novel_id: UUID, locked_only: bool) -> list[dict[str, Any]]:
    db = _get_db()
    where = ["cf.novel_id = %s"]
    params: list[Any] = [str(novel_id)]
    if locked_only:
        where.append("cf.locked = true")
    rows = db.fetchall(
        f"""
        SELECT cf.id, cf.kind, cf.subject_entity_id, e.name AS subject_name,
               cf.predicate, cf.value, cf.source_chapter, cf.confidence, cf.locked
          FROM canon_facts cf
          LEFT JOIN entities e ON e.id = cf.subject_entity_id
         WHERE {' AND '.join(where)}
         ORDER BY cf.locked DESC, e.name NULLS LAST, cf.predicate
        """,
        tuple(params),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "subject_entity_id": r["subject_entity_id"],
            "subject_name": r["subject_name"],
            "predicate": r["predicate"],
            "value": r["value"],
            "source_chapter": r["source_chapter"],
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            "locked": bool(r["locked"]),
        }
        for r in rows
    ]


def update_canon_fact(
    novel_id: UUID, fact_id: UUID, *, locked: bool | None, value: str | None
) -> bool:
    """Patch a canon fact's lock state and/or value in one atomic statement.

    value edits reset confidence to 1.0 (manual entry is authoritative).
    Returns False when the fact doesn't exist in this novel.
    """
    db = _get_db()
    existing = db.fetchone(
        "SELECT id FROM canon_facts WHERE id = %s AND novel_id = %s",
        (str(fact_id), str(novel_id)),
        dict_rows=True,
    )
    if existing is None:
        return False
    db.execute(
        """
        UPDATE canon_facts
           SET locked = COALESCE(%s, locked),
               value = COALESCE(%s, value),
               confidence = CASE WHEN %s::text IS NULL THEN confidence ELSE 1.0 END
         WHERE id = %s AND novel_id = %s
        """,
        (locked, value, value, str(fact_id), str(novel_id)),
    )
    return True


def create_canon_fact(
    novel_id: UUID,
    *,
    subject_entity_id: UUID,
    predicate: str,
    value: str,
    kind: str = "other",
    locked: bool = False,
) -> dict[str, Any] | None:
    """Upsert a canon fact; on conflict OVERWRITES value/locked/confidence.

    Manual entry is authoritative — unlike the pipeline's ON CONFLICT DO
    NOTHING, an admin create deliberately replaces what extraction stored.
    """
    db = _get_db()
    row = db.fetchone(
        """
        INSERT INTO canon_facts (novel_id, kind, subject_entity_id, predicate, value, confidence, locked)
        VALUES (%s, %s, %s, %s, %s, 1.0, %s)
        ON CONFLICT (novel_id, subject_entity_id, predicate)
        DO UPDATE SET value = EXCLUDED.value, locked = EXCLUDED.locked, confidence = 1.0
        RETURNING id, kind, subject_entity_id, predicate, value, source_chapter, confidence, locked
        """,
        (str(novel_id), kind, str(subject_entity_id), predicate.strip().lower(), value, locked),
        dict_rows=True,
        commit=True,
    )
    return dict(row) if row else None


def delete_canon_fact(novel_id: UUID, fact_id: UUID) -> bool:
    db = _get_db()
    existing = db.fetchone(
        "SELECT id FROM canon_facts WHERE id = %s AND novel_id = %s",
        (str(fact_id), str(novel_id)),
        dict_rows=True,
    )
    if existing is None:
        return False
    db.execute(
        "DELETE FROM canon_facts WHERE id = %s AND novel_id = %s",
        (str(fact_id), str(novel_id)),
    )
    return True


def list_knows_edges(
    novel_id: UUID,
    cap: int | None,
    character_id: UUID | None,
) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    where = ["c.novel_id = %s", "k.learned_chapter <= %s", "k.superseded_by_id IS NULL"]
    params: list[Any] = [str(novel_id), effective_cap]
    if character_id is not None:
        where.append("k.character_id = %s")
        params.append(str(character_id))
    rows = db.fetchall(
        f"""
        SELECT k.id, k.character_id, c.name AS character_name,
               k.fact_description, k.learned_chapter, k.source_type,
               k.source_event_id, k.certainty, k.shared_with
          FROM knows_edges k
          JOIN characters c ON c.id = k.character_id
         WHERE {' AND '.join(where)}
         ORDER BY k.learned_chapter, c.name
        """,
        tuple(params),
        dict_rows=True,
    )
    all_shared_ids = sorted({str(cid) for r in rows for cid in (r["shared_with"] or [])})
    name_by_id: dict[str, str] = {}
    if all_shared_ids:
        chars = db.fetchall(
            "SELECT id, name FROM characters WHERE id = ANY(%s::uuid[])",
            (all_shared_ids,),
            dict_rows=True,
        )
        name_by_id = {str(c["id"]): c["name"] for c in chars}
    return [
        {
            "id": r["id"],
            "character_id": r["character_id"],
            "character_name": r["character_name"],
            "fact_description": r["fact_description"],
            "learned_chapter": r["learned_chapter"],
            "source_type": r["source_type"],
            "source_event_id": r["source_event_id"],
            "certainty": float(r["certainty"]) if r["certainty"] is not None else None,
            "shared_with_names": [
                name_by_id.get(str(cid), str(cid)) for cid in (r["shared_with"] or [])
            ],
        }
        for r in rows
    ]


def list_location_edges(
    novel_id: UUID, cap: int | None, only_active: bool
) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    where = ["e.novel_id = %s", "le.since_chapter <= %s"]
    params: list[Any] = [str(novel_id), effective_cap]
    if only_active:
        where.append("(le.until_chapter IS NULL OR le.until_chapter >= %s)")
        params.append(effective_cap)
    rows = db.fetchall(
        f"""
        SELECT le.id, le.entity_id, e.name AS entity_name, e.entity_type,
               le.location_id, loc.name AS location_name,
               le.since_chapter, le.until_chapter, le.certainty
          FROM located_in_edges le
          JOIN entities e ON e.id = le.entity_id
          LEFT JOIN locations loc ON loc.id = le.location_id
         WHERE {' AND '.join(where)}
         ORDER BY le.since_chapter, e.name
        """,
        tuple(params),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "entity_id": r["entity_id"],
            "entity_name": r["entity_name"],
            "entity_type": r["entity_type"],
            "location_id": r["location_id"],
            "location_name": r["location_name"],
            "since_chapter": r["since_chapter"],
            "until_chapter": r["until_chapter"],
            "certainty": float(r["certainty"]) if r["certainty"] is not None else None,
        }
        for r in rows
    ]


def list_possession_edges(
    novel_id: UUID, cap: int | None, only_active: bool
) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    where = ["c.novel_id = %s", "pe.since_chapter <= %s"]
    params: list[Any] = [str(novel_id), effective_cap]
    if only_active:
        where.append("(pe.until_chapter IS NULL OR pe.until_chapter >= %s)")
        params.append(effective_cap)
    rows = db.fetchall(
        f"""
        SELECT pe.id, pe.character_id, c.name AS character_name,
               pe.object_id, o.name AS object_name,
               pe.since_chapter, pe.until_chapter, pe.certainty
          FROM possesses_edges pe
          JOIN characters c ON c.id = pe.character_id
          LEFT JOIN objects o ON o.id = pe.object_id
         WHERE {' AND '.join(where)}
         ORDER BY pe.since_chapter, c.name
        """,
        tuple(params),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "character_id": r["character_id"],
            "character_name": r["character_name"],
            "object_id": r["object_id"],
            "object_name": r["object_name"],
            "since_chapter": r["since_chapter"],
            "until_chapter": r["until_chapter"],
            "certainty": float(r["certainty"]) if r["certainty"] is not None else None,
        }
        for r in rows
    ]
