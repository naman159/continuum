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


def list_novels() -> list[dict[str, Any]]:
    db = _get_db()
    novels = db.novels if hasattr(db, "novels") else _list_novels_real(db)
    chapters = db.chapters if hasattr(db, "chapters") else None

    rows: list[dict[str, Any]] = []
    for novel in novels:
        novel_id = novel["id"]
        if chapters is not None:
            max_chapter = max(
                (c["number"] for c in chapters if c["novel_id"] == novel_id),
                default=0,
            )
        else:
            max_chapter = _max_chapter_real(db, novel_id)
        rows.append(
            {
                "id": novel_id,
                "title": novel["title"],
                "author": novel.get("author"),
                "language": novel.get("language"),
                "created_at": novel["created_at"],
                "max_chapter": max_chapter,
            }
        )
    return rows


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


def _list_novels_real(db: DBClient) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT id, title, author, language, created_at
        FROM novels
        ORDER BY created_at DESC
        """,
        dict_rows=True,
    )
    return [dict(r) for r in rows]


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


def list_characters(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "characters"):
        rows = [
            c
            for c in db.characters
            if c["novel_id"] == novel_id
            and (
                c.get("first_appearance_chapter") is None
                or c["first_appearance_chapter"] <= effective_cap
            )
        ]
    else:
        rows = [
            dict(r)
            for r in db.fetchall(
                """
                SELECT id, name, aliases, description, first_appearance_chapter
                FROM characters
                WHERE novel_id = %s
                  AND (first_appearance_chapter IS NULL OR first_appearance_chapter <= %s)
                ORDER BY name
                """,
                (str(novel_id), effective_cap),
                dict_rows=True,
            )
        ]
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "aliases": list(r.get("aliases") or []),
            "description": r.get("description"),
            "first_appearance_chapter": r.get("first_appearance_chapter"),
        }
        for r in rows
    ]


def get_character_detail(novel_id: UUID, character_id: UUID, cap: int | None) -> dict[str, Any] | None:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "characters"):
        char = next(
            (c for c in db.characters if c["id"] == character_id and c["novel_id"] == novel_id),
            None,
        )
        if char is None:
            return None
        chapter_by_id = {c["id"]: c for c in db.chapters}
        states = [
            s
            for s in db.character_states
            if s["character_id"] == character_id
            and chapter_by_id[s["chapter_id"]]["number"] <= effective_cap
        ]
        states.sort(key=lambda s: chapter_by_id[s["chapter_id"]]["number"])
        events = [
            e
            for e in db.events
            if character_id in e.get("involved_characters", [])
            and chapter_by_id[e["chapter_id"]]["number"] <= effective_cap
        ]
        events.sort(key=lambda e: chapter_by_id[e["chapter_id"]]["number"])
        char_entity_id = char.get("entity_id")
        rels = [
            r
            for r in db.relationships
            if r["entity_a_id"] == char_entity_id or r["entity_b_id"] == char_entity_id
        ]
        # Resolve names
        char_name = {c["id"]: c["name"] for c in db.characters}
        location_name = {l["id"]: l["name"] for l in db.locations}
        object_name = {o["id"]: o["name"] for o in db.objects}
        identity = {
            "id": char["id"],
            "name": char["name"],
            "aliases": list(char.get("aliases") or []),
            "description": char.get("description"),
            "first_appearance_chapter": char.get("first_appearance_chapter"),
        }
    else:
        identity_row = db.fetchone(
            "SELECT id, name, aliases, description, first_appearance_chapter FROM characters WHERE id = %s AND novel_id = %s",
            (str(character_id), str(novel_id)),
            dict_rows=True,
        )
        if identity_row is None:
            return None
        identity = {
            "id": identity_row["id"],
            "name": identity_row["name"],
            "aliases": list(identity_row.get("aliases") or []),
            "description": identity_row.get("description"),
            "first_appearance_chapter": identity_row.get("first_appearance_chapter"),
        }
        states_rows = db.fetchall(
            """
            SELECT cs.*, ch.number AS chapter_number
            FROM character_states cs
            JOIN chapters ch ON ch.id = cs.chapter_id
            WHERE cs.character_id = %s AND ch.number <= %s
            ORDER BY ch.number
            """,
            (str(character_id), effective_cap),
            dict_rows=True,
        )
        events_rows = db.fetchall(
            """
            SELECT e.*, ch.number AS chapter_number
            FROM events e
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE %s = ANY(e.involved_characters) AND ch.number <= %s
            ORDER BY ch.number, e.created_at
            """,
            (str(character_id), effective_cap),
            dict_rows=True,
        )
        rels_rows = db.fetchall(
            """
            SELECT r.id, r.entity_a_id, r.entity_b_id, r.rel_type,
                   r.from_chapter, r.to_chapter, r.notes
            FROM relationships r
            JOIN characters c ON c.entity_id = r.entity_a_id OR c.entity_id = r.entity_b_id
            WHERE c.id = %s
              AND (r.from_chapter IS NULL OR r.from_chapter <= %s)
            """,
            (str(character_id), effective_cap),
            dict_rows=True,
        )
        char_entity_row = db.fetchone(
            "SELECT entity_id FROM characters WHERE id = %s", (str(character_id),)
        )
        char_entity_id = str(char_entity_row[0]) if char_entity_row else None
        # Need names for involved_*; do another fetch
        char_name_rows = db.fetchall("SELECT id, name FROM characters WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        char_name = {r["id"]: r["name"] for r in char_name_rows}
        loc_name_rows = db.fetchall("SELECT id, name FROM locations WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        location_name = {r["id"]: r["name"] for r in loc_name_rows}
        obj_name_rows = db.fetchall("SELECT id, name FROM objects WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        object_name = {r["id"]: r["name"] for r in obj_name_rows}

        chapter_by_id = {}  # not needed in real path
        states = [dict(r) for r in states_rows]
        events = [dict(r) for r in events_rows]
        rels = [dict(r) for r in rels_rows]

    def state_to_row(state: dict[str, Any]) -> dict[str, Any]:
        chapter_number = (
            chapter_by_id[state["chapter_id"]]["number"] if chapter_by_id else state.get("chapter_number")
        )
        loc_id = state.get("location_id")
        return {
            "chapter_number": chapter_number,
            "location": location_name.get(loc_id) if loc_id else None,
            "emotional_state": state.get("emotional_state"),
            "goals": state.get("goals"),
            "knowledge": list(state.get("knowledge") or []),
            "physical_state": state.get("physical_state"),
            "notes": state.get("notes"),
        }

    history = [state_to_row(s) for s in states]
    current_state = history[-1] if history else None

    def event_to_row(event: dict[str, Any]) -> dict[str, Any]:
        chapter_number = (
            chapter_by_id[event["chapter_id"]]["number"] if chapter_by_id else event.get("chapter_number")
        )
        return {
            "id": event["id"],
            "chapter_number": chapter_number,
            "description": event.get("description"),
            "event_type": event.get("event_type"),
            "impact_level": event.get("impact_level"),
            "involved_characters": [char_name.get(cid, str(cid)) for cid in event.get("involved_characters") or []],
            "involved_locations": [location_name.get(lid, str(lid)) for lid in event.get("involved_locations") or []],
            "involved_objects": [object_name.get(oid, str(oid)) for oid in event.get("involved_objects") or []],
        }

    def rel_to_row(rel: dict[str, Any]) -> dict[str, Any]:
        if hasattr(db, "entities"):
            entity_id_map = {e["id"]: e for e in db.entities}
            this_entity_id = char.get("entity_id") if char else None
            if rel["entity_a_id"] == this_entity_id:
                other_universal = rel["entity_b_id"]
                direction = "from"
            else:
                other_universal = rel["entity_a_id"]
                direction = "to"
            other_entity = entity_id_map.get(other_universal, {})
            other_name = other_entity.get("name", str(other_universal))
            other_type = other_entity.get("entity_type", "character")
        else:
            if str(rel["entity_a_id"]) == char_entity_id:
                other_universal = rel["entity_b_id"]
                direction = "from"
            else:
                other_universal = rel["entity_a_id"]
                direction = "to"
            other_entity = db.fetchone(
                "SELECT name, entity_type FROM entities WHERE id = %s",
                (str(other_universal),),
                dict_rows=True,
            )
            other_name = other_entity["name"] if other_entity else str(other_universal)
            other_type = other_entity["entity_type"] if other_entity else "character"
        return {
            "other_entity_id": other_universal,
            "other_entity_name": other_name,
            "other_entity_type": other_type,
            "direction": direction,
            "rel_type": rel.get("rel_type"),
            "from_chapter": rel.get("from_chapter"),
            "to_chapter": rel.get("to_chapter"),
            "notes": rel.get("notes"),
        }

    return {
        "identity": identity,
        "current_state": current_state,
        "history": history,
        "relationships": [rel_to_row(r) for r in rels],
        "events": [event_to_row(e) for e in events],
    }


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
                "SELECT id, number, title, summary, processed_at FROM chapters WHERE novel_id = %s AND number <= %s ORDER BY number",
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
            "processed_at": r.get("processed_at"),
        }
        for r in rows
    ]


def list_timeline(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "chapters"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        char_name = {c["id"]: c["name"] for c in db.characters}
        loc_name = {loc["id"]: loc["name"] for loc in db.locations}
        obj_name = {o["id"]: o["name"] for o in db.objects}
        rows: list[dict[str, Any]] = []
        for e in db.events:
            ch = chapter_by_id.get(e["chapter_id"])
            if ch is None or ch["number"] > effective_cap:
                continue
            rows.append(
                {
                    "id": e["id"],
                    "chapter_number": ch["number"],
                    "description": e["description"],
                    "event_type": e.get("event_type"),
                    "impact_level": e.get("impact_level"),
                    "involved_characters": [char_name.get(c, str(c)) for c in e.get("involved_characters") or []],
                    "involved_locations": [loc_name.get(l, str(l)) for l in e.get("involved_locations") or []],
                    "involved_objects": [obj_name.get(o, str(o)) for o in e.get("involved_objects") or []],
                }
            )
        rows.sort(key=lambda r: r["chapter_number"])
        return rows
    raw = db.fetchall(
        """
        SELECT e.id, e.description, e.event_type, e.impact_level,
               e.involved_characters, e.involved_locations, e.involved_objects,
               ch.number AS chapter_number
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
        ORDER BY ch.number, e.created_at
        """,
        (str(novel_id), effective_cap),
        dict_rows=True,
    )
    char_rows = db.fetchall("SELECT id, name FROM characters WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
    char_name = {r["id"]: r["name"] for r in char_rows}
    loc_rows = db.fetchall("SELECT id, name FROM locations WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
    loc_name = {r["id"]: r["name"] for r in loc_rows}
    obj_rows = db.fetchall("SELECT id, name FROM objects WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
    obj_name = {r["id"]: r["name"] for r in obj_rows}
    return [
        {
            "id": r["id"],
            "chapter_number": r["chapter_number"],
            "description": r["description"],
            "event_type": r.get("event_type"),
            "impact_level": r.get("impact_level"),
            "involved_characters": [char_name.get(c, str(c)) for c in (r.get("involved_characters") or [])],
            "involved_locations": [loc_name.get(l, str(l)) for l in (r.get("involved_locations") or [])],
            "involved_objects": [obj_name.get(o, str(o)) for o in (r.get("involved_objects") or [])],
        }
        for r in raw
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


def get_relationship_graph(novel_id: UUID, cap: int | None) -> dict[str, Any]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "chapters"):
        nodes = [
            {"id": e["id"], "label": e["name"], "description": None}
            for e in db.entities
            if e.get("novel_id") == novel_id and e.get("entity_type") == "character"
            and any(
                c.get("entity_id") == e["id"]
                and (c.get("first_appearance_chapter") is None or c["first_appearance_chapter"] <= effective_cap)
                for c in db.characters
            )
        ]
        entity_id_set = {e["id"] for e in db.entities if e.get("novel_id") == novel_id}
        rels = [
            r for r in db.relationships
            if r["entity_a_id"] in entity_id_set and r["entity_b_id"] in entity_id_set
            and (r.get("from_chapter") is None or r["from_chapter"] <= effective_cap)
        ]

        def rel_chapter(r: dict[str, Any]) -> int | None:
            return r.get("from_chapter")
    else:
        characters = [
            dict(r) for r in db.fetchall(
                """
                SELECT e.id, e.name, NULL AS description, c.first_appearance_chapter
                FROM entities e
                JOIN characters c ON c.entity_id = e.id
                WHERE e.novel_id = %s AND e.entity_type = 'character'
                  AND (c.first_appearance_chapter IS NULL OR c.first_appearance_chapter <= %s)
                """,
                (str(novel_id), effective_cap),
                dict_rows=True,
            )
        ]
        rels_raw = db.fetchall(
            """
            SELECT r.id, r.entity_a_id, r.entity_b_id, r.rel_type, r.from_chapter
            FROM relationships r
            JOIN entities ea ON ea.id = r.entity_a_id AND ea.novel_id = %s AND ea.entity_type = 'character'
            JOIN entities eb ON eb.id = r.entity_b_id AND eb.entity_type = 'character'
            WHERE (r.from_chapter IS NULL OR r.from_chapter <= %s)
            """,
            (str(novel_id), effective_cap),
            dict_rows=True,
        )
        rels = [dict(r) for r in rels_raw]

        def rel_chapter(r: dict[str, Any]) -> int | None:
            return r.get("from_chapter")

        nodes = [
            {"id": c["id"], "label": c["name"], "description": c.get("description")}
            for c in characters
        ]

    character_id_set = {n["id"] for n in nodes}
    edges = [
        {
            "from": r["entity_a_id"],
            "to": r["entity_b_id"],
            "id": r["id"],
            "label": r.get("rel_type"),
            "chapter_number": rel_chapter(r),
        }
        for r in rels
        if r["entity_a_id"] in character_id_set and r["entity_b_id"] in character_id_set
    ]
    return {"nodes": nodes, "edges": edges}


def list_continuity(novel_id: UUID, cap: int | None, resolved_filter: str) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "continuity_flags"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        flags: list[dict[str, Any]] = []
        for f in db.continuity_flags:
            ch = chapter_by_id.get(f["chapter_id"])
            if ch is None or ch["number"] > effective_cap:
                continue
            resolved_at_id = f.get("resolved_chapter_id")
            resolved_chapter_number = (
                chapter_by_id[resolved_at_id]["number"]
                if resolved_at_id and resolved_at_id in chapter_by_id
                else None
            )
            effectively_resolved = bool(f.get("resolved")) and (
                resolved_chapter_number is None or resolved_chapter_number <= effective_cap
            )
            if resolved_filter == "open" and effectively_resolved:
                continue
            flags.append(
                {
                    "id": f["id"],
                    "chapter_number": ch["number"],
                    "description": f["description"],
                    "flag_type": f.get("flag_type"),
                    "resolved": effectively_resolved,
                    "resolved_chapter_number": resolved_chapter_number if effectively_resolved else None,
                }
            )
        return flags
    raw = db.fetchall(
        """
        SELECT cf.id, cf.description, cf.flag_type, cf.resolved, cf.resolved_chapter_id,
               ch.number AS chapter_number
        FROM continuity_flags cf
        JOIN chapters ch ON ch.id = cf.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
        ORDER BY ch.number
        """,
        (str(novel_id), effective_cap),
        dict_rows=True,
    )
    chap_lookup = {
        r["id"]: r["number"]
        for r in db.fetchall(
            "SELECT id, number FROM chapters WHERE novel_id = %s", (str(novel_id),), dict_rows=True
        )
    }
    out: list[dict[str, Any]] = []
    for r in raw:
        resolved_at_id = r.get("resolved_chapter_id")
        resolved_chapter_number = chap_lookup.get(resolved_at_id)
        effectively_resolved = bool(r.get("resolved")) and (
            resolved_chapter_number is None or resolved_chapter_number <= effective_cap
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


def list_shared_dynamics(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "shared_dynamics"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        rows: list[dict[str, Any]] = []
        for dyn in db.shared_dynamics:
            ch = chapter_by_id.get(dyn["chapter_id"])
            if ch is None or ch["number"] > effective_cap:
                continue
            rows.append({
                "id": dyn["id"],
                "entity_a_id": dyn["entity_a_id"],
                "entity_b_id": dyn["entity_b_id"],
                "chapter_number": ch["number"],
                "description": dyn.get("description"),
            })
        rows.sort(key=lambda r: r["chapter_number"])
        return rows

    raw = db.fetchall(
        """
        SELECT sd.id, sd.entity_a_id, sd.entity_b_id, sd.description, ch.number AS chapter_number
        FROM shared_dynamics sd
        JOIN chapters ch ON ch.id = sd.chapter_id
        WHERE ch.novel_id = %s AND ch.number <= %s
        ORDER BY ch.number
        """,
        (str(novel_id), effective_cap),
        dict_rows=True,
    )
    return [
        {
            "id": r["id"],
            "entity_a_id": r["entity_a_id"],
            "entity_b_id": r["entity_b_id"],
            "chapter_number": r["chapter_number"],
            "description": r.get("description"),
        }
        for r in raw
    ]
