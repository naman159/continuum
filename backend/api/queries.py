from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pipeline.db.client import DBClient


_db: DBClient | None = None

_STORY_KIND_PRECEDENCE = ["dynamic", "event", "possession", "location"]


def _merge_story_edges(raw: list[dict]) -> list[dict]:
    """Collapse multiple raw story records between the same entity pair into one edge.

    Each item in raw must have: from (str), to (str), edge_kind (str), description (str|None).
    Returns one dict per canonical pair with keys: id, from, to, label, chapter_number,
    edge_kind, tooltip.
    """
    grouped: dict[tuple[str, str], dict] = {}
    for item in raw:
        a, b = item["from"], item["to"]
        pair = (min(a, b), max(a, b))
        desc = item.get("description") or ""
        if pair not in grouped:
            grouped[pair] = {
                "from": a,
                "to": b,
                "edge_kind": item["edge_kind"],
                "descriptions": [desc] if desc else [],
            }
        else:
            existing = grouped[pair]
            cur_prec = _STORY_KIND_PRECEDENCE.index(existing["edge_kind"])
            new_prec = _STORY_KIND_PRECEDENCE.index(item["edge_kind"])
            if new_prec < cur_prec:
                existing["edge_kind"] = item["edge_kind"]
            if desc:
                existing["descriptions"].append(desc)

    result = []
    for data in grouped.values():
        n = len(data["descriptions"])
        kind = data["edge_kind"]
        kind_plural = {
            "dynamic": "dynamics", "event": "events",
            "possession": "possessions", "location": "locations",
        }.get(kind, kind)
        label = data["descriptions"][0] if n == 1 else (f"{n} {kind_plural}" if n > 0 else None)
        tooltip = "\n".join(data["descriptions"]) or None
        result.append({
            "id": str(uuid4()),
            "from": data["from"],
            "to": data["to"],
            "label": label,
            "chapter_number": None,
            "edge_kind": kind,
            "tooltip": tooltip,
        })
    return result


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


def create_novel(
    title: str,
    author: str | None,
    language: str | None,
    custom_entity_types: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    db = _get_db()
    if hasattr(db, "novels"):
        novel: dict[str, Any] = {
            "id": uuid4(),
            "title": title,
            "author": author,
            "language": language,
            "created_at": datetime.now(timezone.utc),
        }
        db.novels.append(novel)
        for et in (custom_entity_types or []):
            db.novel_entity_types.append({
                "id": uuid4(),
                "novel_id": novel["id"],
                "name": et["name"],
                "description": et.get("description"),
            })
        return {**novel, "max_chapter": 0}
    return _create_novel_real(db, title, author, language, custom_entity_types or [])


def _create_novel_real(
    db: DBClient,
    title: str,
    author: str | None,
    language: str | None,
    custom_entity_types: list[dict[str, Any]],
) -> dict[str, Any]:
    row = db.fetchone(
        """
        INSERT INTO novels (id, title, author, language, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        RETURNING id, title, author, language, created_at
        """,
        (str(uuid4()), title, author, language),
        dict_rows=True,
        commit=True,
    )
    novel_id = str(row["id"])
    for et in custom_entity_types:
        db.execute(
            """
            INSERT INTO novel_entity_types (novel_id, name, description)
            VALUES (%s, %s, %s)
            ON CONFLICT (novel_id, name) DO NOTHING
            """,
            (novel_id, et["name"], et.get("description")),
            commit=True,
        )
    return {**dict(row), "max_chapter": 0}


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
        char_entity_ids = {c["entity_id"] for c in db.characters if c.get("entity_id") is not None}
        rels = [
            r
            for r in db.relationships
            if (r["entity_a_id"] == char_entity_id or r["entity_b_id"] == char_entity_id)
            and r["entity_a_id"] in char_entity_ids
            and r["entity_b_id"] in char_entity_ids
        ]
        # Resolve names
        char_name = {c["id"]: c["name"] for c in db.characters}
        location_name = {l["id"]: l["name"] for l in db.locations}
        object_name = {o["id"]: o["name"] for o in db.objects}
        faction_name = {f["id"]: f["name"] for f in db.factions}
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
            SELECT e.id, e.description, e.event_type, e.impact_level,
                   e.involved_characters, e.involved_locations, e.involved_objects, e.involved_factions,
                   ch.number AS chapter_number
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
            JOIN entities ea ON ea.id = r.entity_a_id AND ea.entity_type = 'character'
            JOIN entities eb ON eb.id = r.entity_b_id AND eb.entity_type = 'character'
            JOIN characters c ON c.entity_id = ea.id OR c.entity_id = eb.id
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
        faction_rows = db.fetchall("SELECT id, name FROM factions WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        faction_name = {r["id"]: r["name"] for r in faction_rows}

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
            "appearance": state.get("appearance"),
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
            "involved_factions": [faction_name.get(fid, str(fid)) for fid in (event.get("involved_factions") or [])],
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
            "other_entity_name": other_name,
            "other_entity_type": other_type,
            "direction": direction,
            "rel_type": rel.get("rel_type"),
            "from_chapter": rel.get("from_chapter"),
            "to_chapter": rel.get("to_chapter"),
            "notes": rel.get("notes"),
        }

    # Fetch shared dynamics involving this character
    if hasattr(db, "shared_dynamics"):
        entity_by_id = {e["id"]: e for e in db.entities}
        char_entity_id_for_dyn = char.get("entity_id") if hasattr(db, "characters") else char_entity_id
        dyn_rows_raw = [
            d for d in db.shared_dynamics
            if (d["entity_a_id"] == char_entity_id_for_dyn or d["entity_b_id"] == char_entity_id_for_dyn)
            and chapter_by_id.get(d["chapter_id"], {}).get("number", 0) <= effective_cap
        ]
        dynamics = [
            {
                "id": d["id"],
                "chapter_number": chapter_by_id[d["chapter_id"]]["number"],
                "other_entity_name": entity_by_id.get(
                    d["entity_b_id"] if d["entity_a_id"] == char_entity_id_for_dyn else d["entity_a_id"],
                    {},
                ).get("name", ""),
                "other_entity_type": entity_by_id.get(
                    d["entity_b_id"] if d["entity_a_id"] == char_entity_id_for_dyn else d["entity_a_id"],
                    {},
                ).get("entity_type", "character"),
                "description": d.get("description"),
            }
            for d in sorted(dyn_rows_raw, key=lambda d: chapter_by_id[d["chapter_id"]]["number"])
        ]
    else:
        dyn_rows = db.fetchall(
            """
            SELECT sd.id, ch.number AS chapter_number,
                   e_other.name AS other_entity_name,
                   e_other.entity_type AS other_entity_type,
                   sd.description
            FROM shared_dynamics sd
            JOIN chapters ch ON ch.id = sd.chapter_id
            JOIN characters c ON (c.entity_id = sd.entity_a_id OR c.entity_id = sd.entity_b_id)
            JOIN entities e_other ON e_other.id = (
                CASE WHEN c.entity_id = sd.entity_a_id THEN sd.entity_b_id ELSE sd.entity_a_id END
            )
            WHERE c.id = %s AND ch.novel_id = %s AND ch.number <= %s
            ORDER BY ch.number
            """,
            (str(character_id), str(novel_id), effective_cap),
            dict_rows=True,
        )
        dynamics = [dict(r) for r in dyn_rows]

    return {
        "identity": identity,
        "current_state": current_state,
        "history": history,
        "relationships": [rel_to_row(r) for r in rels],
        "dynamics": dynamics,
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


def list_timeline(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "chapters"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        char_name = {c["id"]: c["name"] for c in db.characters}
        loc_name = {loc["id"]: loc["name"] for loc in db.locations}
        obj_name = {o["id"]: o["name"] for o in db.objects}
        faction_name = {f["id"]: f["name"] for f in db.factions}
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
                    "involved_factions": [faction_name.get(fid, str(fid)) for fid in (e.get("involved_factions") or [])],
                }
            )
        rows.sort(key=lambda r: r["chapter_number"])
        return rows
    raw = db.fetchall(
        """
        SELECT e.id, e.description, e.event_type, e.impact_level,
               e.involved_characters, e.involved_locations, e.involved_objects, e.involved_factions,
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
    faction_rows = db.fetchall("SELECT id, name FROM factions WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
    faction_name = {r["id"]: r["name"] for r in faction_rows}
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
            "involved_factions": [faction_name.get(fid, str(fid)) for fid in (r.get("involved_factions") or [])],
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
        # Build entity_id -> character lookup for mapping entities.id -> characters.id
        char_by_entity_id = {
            c["entity_id"]: c for c in db.characters
            if c.get("entity_id") is not None
        }
        nodes = [
            {"id": char_by_entity_id[e["id"]]["id"], "label": e["name"], "description": None}
            for e in db.entities
            if e.get("novel_id") == novel_id and e.get("entity_type") == "character"
            and e["id"] in char_by_entity_id
            and (
                char_by_entity_id[e["id"]].get("first_appearance_chapter") is None
                or char_by_entity_id[e["id"]]["first_appearance_chapter"] <= effective_cap
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

        character_id_set = {n["id"] for n in nodes}
        edges = [
            {
                "from": char_by_entity_id[r["entity_a_id"]]["id"],
                "to": char_by_entity_id[r["entity_b_id"]]["id"],
                "id": r["id"],
                "label": r.get("rel_type"),
                "chapter_number": rel_chapter(r),
            }
            for r in rels
            if r["entity_a_id"] in char_by_entity_id and r["entity_b_id"] in char_by_entity_id
            and char_by_entity_id[r["entity_a_id"]]["id"] in character_id_set
            and char_by_entity_id[r["entity_b_id"]]["id"] in character_id_set
        ]
    else:
        characters = [
            dict(r) for r in db.fetchall(
                """
                SELECT c.id, e.name, NULL AS description, c.first_appearance_chapter
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
            SELECT r.id, ca.id AS char_a_id, cb.id AS char_b_id, r.rel_type, r.from_chapter
            FROM relationships r
            JOIN entities ea ON ea.id = r.entity_a_id AND ea.novel_id = %s AND ea.entity_type = 'character'
            JOIN entities eb ON eb.id = r.entity_b_id AND eb.entity_type = 'character'
            JOIN characters ca ON ca.entity_id = ea.id
            JOIN characters cb ON cb.entity_id = eb.id
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
                "from": r["char_a_id"],
                "to": r["char_b_id"],
                "id": r["id"],
                "label": r.get("rel_type"),
                "chapter_number": rel_chapter(r),
            }
            for r in rels
            if r["char_a_id"] in character_id_set and r["char_b_id"] in character_id_set
        ]

    edge_node_ids = {str(e["from"]) for e in edges} | {str(e["to"]) for e in edges}
    nodes = [n for n in nodes if str(n["id"]) in edge_node_ids]
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


def list_locations(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "locations"):
        rows = [
            loc for loc in db.locations
            if loc["novel_id"] == novel_id
            and (loc.get("first_appearance_chapter") is None or loc["first_appearance_chapter"] <= effective_cap)
        ]
    else:
        rows = [
            dict(r)
            for r in db.fetchall(
                """
                SELECT id, name, aliases, description, first_appearance_chapter
                FROM locations
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


def get_location_detail(novel_id: UUID, location_id: UUID, cap: int | None) -> dict[str, Any] | None:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "locations"):
        loc = next(
            (l for l in db.locations if l["id"] == location_id and l["novel_id"] == novel_id),
            None,
        )
        if loc is None:
            return None
        char_name = {c["id"]: c["name"] for c in db.characters}
        loc_name = {l["id"]: l["name"] for l in db.locations}
        obj_name = {o["id"]: o["name"] for o in db.objects}
        faction_name = {f["id"]: f["name"] for f in db.factions}
        char_ids_at_loc = {cs["character_id"] for cs in db.character_states if cs.get("location_id") == location_id}
        events = [
            {
                "id": e["id"],
                "chapter_number": e.get("chapter_number", 0),
                "description": e["description"],
                "event_type": e.get("event_type"),
                "impact_level": e.get("impact_level"),
                "involved_characters": [char_name.get(cid, str(cid)) for cid in (e.get("involved_characters") or [])],
                "involved_locations": [loc_name.get(lid, str(lid)) for lid in (e.get("involved_locations") or [])],
                "involved_objects": [obj_name.get(oid, str(oid)) for oid in (e.get("involved_objects") or [])],
                "involved_factions": [faction_name.get(fid, str(fid)) for fid in (e.get("involved_factions") or [])],
            }
            for e in db.events
            if location_id in (e.get("involved_locations") or [])
        ]
        characters = sorted(char_name.get(cid, str(cid)) for cid in char_ids_at_loc)
    else:
        row = db.fetchone(
            """
            SELECT id, name, aliases, description, first_appearance_chapter
            FROM locations WHERE novel_id = %s AND id = %s
            """,
            (str(novel_id), str(location_id)),
            dict_rows=True,
        )
        if row is None:
            return None
        loc = dict(row)

        char_name_rows = db.fetchall("SELECT id, name FROM characters WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        loc_name_rows = db.fetchall("SELECT id, name FROM locations WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        obj_name_rows = db.fetchall("SELECT id, name FROM objects WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        faction_rows = db.fetchall("SELECT id, name FROM factions WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        char_name = {r["id"]: r["name"] for r in char_name_rows}
        loc_name = {r["id"]: r["name"] for r in loc_name_rows}
        obj_name = {r["id"]: r["name"] for r in obj_name_rows}
        faction_name = {r["id"]: r["name"] for r in faction_rows}

        event_rows = db.fetchall(
            """
            SELECT e.id, e.description, e.event_type, e.impact_level,
                   ch.number AS chapter_number,
                   e.involved_characters, e.involved_locations, e.involved_objects, e.involved_factions
            FROM events e
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE ch.novel_id = %s AND ch.number <= %s
              AND %s::uuid = ANY(e.involved_locations)
            ORDER BY ch.number
            """,
            (str(novel_id), effective_cap, str(location_id)),
            dict_rows=True,
        )
        events = [
            {
                "id": r["id"],
                "chapter_number": r["chapter_number"],
                "description": r["description"],
                "event_type": r.get("event_type"),
                "impact_level": r.get("impact_level"),
                "involved_characters": [char_name.get(cid, str(cid)) for cid in (r.get("involved_characters") or [])],
                "involved_locations": [loc_name.get(lid, str(lid)) for lid in (r.get("involved_locations") or [])],
                "involved_objects": [obj_name.get(oid, str(oid)) for oid in (r.get("involved_objects") or [])],
                "involved_factions": [faction_name.get(fid, str(fid)) for fid in (r.get("involved_factions") or [])],
            }
            for r in event_rows
        ]

        char_rows = db.fetchall(
            """
            SELECT DISTINCT c.name
            FROM character_states cs
            JOIN characters c ON c.id = cs.character_id
            JOIN chapters ch ON ch.id = cs.chapter_id
            WHERE c.novel_id = %s AND cs.location_id = %s AND ch.number <= %s
            ORDER BY c.name
            """,
            (str(novel_id), str(location_id), effective_cap),
            dict_rows=True,
        )
        characters = [r["name"] for r in char_rows]

    return {
        "identity": {
            "id": loc["id"],
            "name": loc["name"],
            "aliases": list(loc.get("aliases") or []),
            "description": loc.get("description"),
            "first_appearance_chapter": loc.get("first_appearance_chapter"),
        },
        "events": events,
        "characters": characters,
    }


def list_objects(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    if hasattr(db, "objects"):
        rows = [
            o for o in db.objects
            if o["novel_id"] == novel_id
            and (o.get("first_appearance_chapter") is None or o["first_appearance_chapter"] <= effective_cap)
        ]
    else:
        rows = [
            dict(r)
            for r in db.fetchall(
                """
                SELECT id, name, aliases, description, significance, first_appearance_chapter
                FROM objects
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
            "significance": r.get("significance"),
            "first_appearance_chapter": r.get("first_appearance_chapter"),
        }
        for r in rows
    ]


def get_object_detail(novel_id: UUID, object_id: UUID, cap: int | None) -> dict[str, Any] | None:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "objects"):
        obj = next(
            (o for o in db.objects if o["id"] == object_id and o["novel_id"] == novel_id),
            None,
        )
        if obj is None:
            return None
        obj_entity_id = obj.get("entity_id")
        char_name = {c["id"]: c["name"] for c in db.characters}
        char_entity_name = {c["entity_id"]: c["name"] for c in db.characters if c.get("entity_id")}
        loc_name = {l["id"]: l["name"] for l in db.locations}
        obj_name_map = {o["id"]: o["name"] for o in db.objects}
        faction_name = {f["id"]: f["name"] for f in db.factions}
        events = [
            {
                "id": e["id"],
                "chapter_number": e.get("chapter_number", 0),
                "description": e["description"],
                "event_type": e.get("event_type"),
                "impact_level": e.get("impact_level"),
                "involved_characters": [char_name.get(cid, str(cid)) for cid in (e.get("involved_characters") or [])],
                "involved_locations": [loc_name.get(lid, str(lid)) for lid in (e.get("involved_locations") or [])],
                "involved_objects": [obj_name_map.get(oid, str(oid)) for oid in (e.get("involved_objects") or [])],
                "involved_factions": [faction_name.get(fid, str(fid)) for fid in (e.get("involved_factions") or [])],
            }
            for e in db.events
            if object_id in (e.get("involved_objects") or [])
        ]
        involved_char_ids: set[Any] = set()
        for e in db.events:
            if object_id in (e.get("involved_objects") or []):
                involved_char_ids.update(e.get("involved_characters") or [])
        characters = sorted(char_name.get(cid, str(cid)) for cid in involved_char_ids)
        relationships = [
            {
                "character_name": char_entity_name.get(r["entity_a_id"], str(r["entity_a_id"])),
                "rel_type": r.get("rel_type"),
                "from_chapter": r.get("from_chapter"),
                "to_chapter": r.get("to_chapter"),
                "notes": r.get("notes"),
            }
            for r in db.relationships
            if r.get("entity_b_id") == obj_entity_id and obj_entity_id is not None
        ]
    else:
        row = db.fetchone(
            """
            SELECT id, name, aliases, description, significance,
                   first_appearance_chapter, entity_id
            FROM objects WHERE novel_id = %s AND id = %s
            """,
            (str(novel_id), str(object_id)),
            dict_rows=True,
        )
        if row is None:
            return None
        obj = dict(row)

        char_name_rows = db.fetchall("SELECT id, name FROM characters WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        loc_name_rows = db.fetchall("SELECT id, name FROM locations WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        obj_name_rows = db.fetchall("SELECT id, name FROM objects WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        faction_rows = db.fetchall("SELECT id, name FROM factions WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        char_name = {r["id"]: r["name"] for r in char_name_rows}
        loc_name = {r["id"]: r["name"] for r in loc_name_rows}
        obj_name_map = {r["id"]: r["name"] for r in obj_name_rows}
        faction_name = {r["id"]: r["name"] for r in faction_rows}

        event_rows = db.fetchall(
            """
            SELECT e.id, e.description, e.event_type, e.impact_level,
                   ch.number AS chapter_number,
                   e.involved_characters, e.involved_locations, e.involved_objects, e.involved_factions
            FROM events e
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE ch.novel_id = %s AND ch.number <= %s
              AND %s::uuid = ANY(e.involved_objects)
            ORDER BY ch.number
            """,
            (str(novel_id), effective_cap, str(object_id)),
            dict_rows=True,
        )
        events = [
            {
                "id": r["id"],
                "chapter_number": r["chapter_number"],
                "description": r["description"],
                "event_type": r.get("event_type"),
                "impact_level": r.get("impact_level"),
                "involved_characters": [char_name.get(cid, str(cid)) for cid in (r.get("involved_characters") or [])],
                "involved_locations": [loc_name.get(lid, str(lid)) for lid in (r.get("involved_locations") or [])],
                "involved_objects": [obj_name_map.get(oid, str(oid)) for oid in (r.get("involved_objects") or [])],
                "involved_factions": [faction_name.get(fid, str(fid)) for fid in (r.get("involved_factions") or [])],
            }
            for r in event_rows
        ]

        char_ids_in_events: set[str] = set()
        for r in event_rows:
            char_ids_in_events.update(str(c) for c in (r.get("involved_characters") or []))
        characters = sorted(char_name.get(cid, cid) for cid in char_ids_in_events)

        rel_rows = db.fetchall(
            """
            SELECT c.name AS character_name, r.rel_type, r.from_chapter, r.to_chapter, r.notes
            FROM relationships r
            JOIN characters c ON c.entity_id = r.entity_a_id AND c.novel_id = %s
            WHERE r.entity_b_id = (
                SELECT entity_id FROM objects WHERE id = %s AND novel_id = %s
            )
            ORDER BY r.from_chapter NULLS LAST
            """,
            (str(novel_id), str(object_id), str(novel_id)),
            dict_rows=True,
        )
        relationships = [dict(r) for r in rel_rows]

    return {
        "identity": {
            "id": obj["id"],
            "name": obj["name"],
            "aliases": list(obj.get("aliases") or []),
            "description": obj.get("description"),
            "significance": obj.get("significance"),
            "first_appearance_chapter": obj.get("first_appearance_chapter"),
        },
        "events": events,
        "characters": characters,
        "relationships": relationships,
    }


def list_factions(novel_id: UUID) -> list[dict[str, Any]]:
    db = _get_db()
    if hasattr(db, "factions"):
        rows = [f for f in db.factions if f["novel_id"] == novel_id]
    else:
        rows = [
            dict(r)
            for r in db.fetchall(
                """
                SELECT id, name, aliases, description
                FROM factions
                WHERE novel_id = %s
                ORDER BY name
                """,
                (str(novel_id),),
                dict_rows=True,
            )
        ]
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "aliases": list(r.get("aliases") or []),
            "description": r.get("description"),
        }
        for r in rows
    ]


def get_faction_detail(novel_id: UUID, faction_id: UUID) -> dict[str, Any] | None:
    db = _get_db()
    if hasattr(db, "factions"):
        faction = next(
            (f for f in db.factions if f["id"] == faction_id and f["novel_id"] == novel_id),
            None,
        )
        if faction is None:
            return None
        faction_id_local = faction["id"]
        char_name = {c["id"]: c["name"] for c in db.characters}
        loc_name = {l["id"]: l["name"] for l in db.locations}
        obj_name = {o["id"]: o["name"] for o in db.objects}
        faction_name_map = {f["id"]: f["name"] for f in db.factions}
        events = [
            {
                "id": e["id"],
                "chapter_number": e.get("chapter_number", 0),
                "description": e["description"],
                "event_type": e.get("event_type"),
                "impact_level": e.get("impact_level"),
                "involved_characters": [char_name.get(cid, str(cid)) for cid in (e.get("involved_characters") or [])],
                "involved_locations": [loc_name.get(lid, str(lid)) for lid in (e.get("involved_locations") or [])],
                "involved_objects": [obj_name.get(oid, str(oid)) for oid in (e.get("involved_objects") or [])],
                "involved_factions": [faction_name_map.get(fid, str(fid)) for fid in (e.get("involved_factions") or [])],
            }
            for e in db.events
            if faction_id_local in (e.get("involved_factions") or [])
        ]
    else:
        row = db.fetchone(
            """
            SELECT id, name, aliases, description
            FROM factions WHERE novel_id = %s AND id = %s
            """,
            (str(novel_id), str(faction_id)),
            dict_rows=True,
        )
        if row is None:
            return None
        faction = dict(row)

        char_name_rows = db.fetchall("SELECT id, name FROM characters WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        loc_name_rows = db.fetchall("SELECT id, name FROM locations WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        obj_name_rows = db.fetchall("SELECT id, name FROM objects WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        faction_name_rows = db.fetchall("SELECT id, name FROM factions WHERE novel_id = %s", (str(novel_id),), dict_rows=True)
        char_name = {r["id"]: r["name"] for r in char_name_rows}
        loc_name = {r["id"]: r["name"] for r in loc_name_rows}
        obj_name = {r["id"]: r["name"] for r in obj_name_rows}
        faction_name_map = {r["id"]: r["name"] for r in faction_name_rows}

        faction_row = db.fetchone(
            "SELECT entity_id FROM factions WHERE id = %s AND novel_id = %s",
            (str(faction_id), str(novel_id)),
        )
        faction_entity_id = str(faction_row[0]) if faction_row else None

        if faction_entity_id:
            event_rows = db.fetchall(
                """
                SELECT e.id, e.description, e.event_type, e.impact_level,
                       ch.number AS chapter_number,
                       e.involved_characters, e.involved_locations, e.involved_objects, e.involved_factions
                FROM events e
                JOIN chapters ch ON ch.id = e.chapter_id
                WHERE ch.novel_id = %s
                  AND %s::uuid = ANY(e.involved_factions)
                ORDER BY ch.number
                """,
                (str(novel_id), faction_entity_id),
                dict_rows=True,
            )
            events = [
                {
                    "id": r["id"],
                    "chapter_number": r["chapter_number"],
                    "description": r["description"],
                    "event_type": r.get("event_type"),
                    "impact_level": r.get("impact_level"),
                    "involved_characters": [char_name.get(cid, str(cid)) for cid in (r.get("involved_characters") or [])],
                    "involved_locations": [loc_name.get(lid, str(lid)) for lid in (r.get("involved_locations") or [])],
                    "involved_objects": [obj_name.get(oid, str(oid)) for oid in (r.get("involved_objects") or [])],
                    "involved_factions": [faction_name_map.get(fid, str(fid)) for fid in (r.get("involved_factions") or [])],
                }
                for r in event_rows
            ]
        else:
            events = []

    return {
        "identity": {
            "id": faction["id"],
            "name": faction["name"],
            "aliases": list(faction.get("aliases") or []),
            "description": faction.get("description"),
        },
        "events": events,
        "characters": [],
    }


def list_shared_dynamics(novel_id: UUID, cap: int | None) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "shared_dynamics"):
        chapter_by_id = {c["id"]: c for c in db.chapters if c["novel_id"] == novel_id}
        entity_by_id = {e["id"]: e for e in db.entities} if hasattr(db, "entities") else {}
        rows: list[dict[str, Any]] = []
        for dyn in db.shared_dynamics:
            ch = chapter_by_id.get(dyn["chapter_id"])
            if ch is None or ch["number"] > effective_cap:
                continue
            ea = entity_by_id.get(dyn["entity_a_id"], {})
            eb = entity_by_id.get(dyn["entity_b_id"], {})
            rows.append({
                "id": dyn["id"],
                "entity_a_id": dyn["entity_a_id"],
                "entity_a_name": ea.get("name", str(dyn["entity_a_id"])[:8]),
                "entity_b_id": dyn["entity_b_id"],
                "entity_b_name": eb.get("name", str(dyn["entity_b_id"])[:8]),
                "chapter_number": ch["number"],
                "description": dyn.get("description"),
            })
        rows.sort(key=lambda r: r["chapter_number"])
        return rows

    raw = db.fetchall(
        """
        SELECT sd.id, sd.entity_a_id, ea.name AS entity_a_name,
               sd.entity_b_id, eb.name AS entity_b_name,
               sd.description, ch.number AS chapter_number
        FROM shared_dynamics sd
        JOIN chapters ch ON ch.id = sd.chapter_id
        JOIN entities ea ON ea.id = sd.entity_a_id
        JOIN entities eb ON eb.id = sd.entity_b_id
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
            "entity_a_name": r["entity_a_name"],
            "entity_b_id": r["entity_b_id"],
            "entity_b_name": r["entity_b_name"],
            "chapter_number": r["chapter_number"],
            "description": r.get("description"),
        }
        for r in raw
    ]


# ============================================================================
# SOTA-upgrade queries: scenes, knows, commitments, canon, location/possession.
# These use the real DB only (the in-memory fake DB used by some tests does not
# know about these tables).
# ============================================================================


def list_scenes(
    novel_id: UUID, cap: int | None, chapter_number: int | None
) -> list[dict[str, Any]]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)
    where = ["ch.novel_id = %s", "ch.number <= %s"]
    params: list[Any] = [str(novel_id), effective_cap]
    if chapter_number is not None:
        where.append("ch.number = %s")
        params.append(chapter_number)
    rows = db.fetchall(
        f"""
        SELECT s.id, s.chapter_id, ch.number AS chapter_number, s.scene_index,
               s.pov_character_id, pov.name AS pov_character_name,
               s.location_id, loc.name AS location_name,
               s.time_anchor, s.story_time_ordinal, s.summary,
               s.present_characters
          FROM scenes s
          JOIN chapters ch ON ch.id = s.chapter_id
          LEFT JOIN characters pov ON pov.id = s.pov_character_id
          LEFT JOIN locations loc ON loc.id = s.location_id
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
            "SELECT id, name FROM characters WHERE id = ANY(%s::uuid[])",
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


def list_entity_types(novel_id: UUID) -> list[dict[str, Any]]:
    db = _get_db()
    if hasattr(db, "novel_entity_types"):
        return [
            {
                "id": str(et["id"]),
                "novel_id": str(et["novel_id"]),
                "name": et["name"],
                "description": et.get("description"),
            }
            for et in db.novel_entity_types
            if et["novel_id"] == novel_id
        ]
    rows = db.fetchall(
        "SELECT id, novel_id, name, description FROM novel_entity_types WHERE novel_id = %s ORDER BY name",
        (str(novel_id),),
        dict_rows=True,
    )
    return [dict(r) for r in rows]


def list_custom_entities(novel_id: UUID, entity_type: str) -> list[dict[str, Any]]:
    db = _get_db()
    if hasattr(db, "entities"):
        return [
            {
                "id": str(e["id"]),
                "name": e["name"],
                "entity_type": e["entity_type"],
                "description": e.get("description"),
            }
            for e in db.entities
            if e.get("novel_id") == novel_id and e.get("entity_type") == entity_type
        ]
    rows = db.fetchall(
        """
        SELECT id, name, entity_type, NULL AS description
        FROM entities
        WHERE novel_id = %s AND entity_type = %s
        ORDER BY name
        """,
        (str(novel_id), entity_type),
        dict_rows=True,
    )
    return [
        {"id": str(r["id"]), "name": r["name"], "entity_type": r["entity_type"], "description": r.get("description")}
        for r in rows
    ]


def get_custom_entity_detail(novel_id: UUID, entity_id: UUID) -> dict[str, Any] | None:
    db = _get_db()
    if hasattr(db, "entities"):
        entity = next(
            (e for e in db.entities if e["id"] == entity_id and e.get("novel_id") == novel_id),
            None,
        )
        if entity is None:
            return None
        entity_id_str = str(entity_id)
        rels = [
            r for r in db.relationships
            if str(r["entity_a_id"]) == entity_id_str or str(r["entity_b_id"]) == entity_id_str
        ]
        entity_by_id = {str(e["id"]): e for e in db.entities}
        relationships = []
        for r in rels:
            if str(r["entity_a_id"]) == entity_id_str:
                other_id = str(r["entity_b_id"])
                direction = "from"
            else:
                other_id = str(r["entity_a_id"])
                direction = "to"
            other = entity_by_id.get(other_id, {})
            relationships.append({
                "other_entity_name": other.get("name", other_id),
                "other_entity_type": other.get("entity_type", "unknown"),
                "direction": direction,
                "rel_type": r.get("rel_type"),
                "from_chapter": r.get("from_chapter"),
                "to_chapter": r.get("to_chapter"),
                "notes": r.get("notes"),
            })
        return {
            "id": entity_id_str,
            "name": entity["name"],
            "entity_type": entity["entity_type"],
            "description": entity.get("description"),
            "relationships": relationships,
        }
    row = db.fetchone(
        "SELECT id, name, entity_type FROM entities WHERE id = %s AND novel_id = %s",
        (str(entity_id), str(novel_id)),
        dict_rows=True,
    )
    if row is None:
        return None
    rels_rows = db.fetchall(
        """
        SELECT r.entity_a_id, r.entity_b_id, r.rel_type, r.from_chapter, r.to_chapter, r.notes,
               ea.name AS name_a, ea.entity_type AS type_a,
               eb.name AS name_b, eb.entity_type AS type_b
        FROM relationships r
        JOIN entities ea ON ea.id = r.entity_a_id
        JOIN entities eb ON eb.id = r.entity_b_id
        WHERE r.entity_a_id = %s OR r.entity_b_id = %s
        """,
        (str(entity_id), str(entity_id)),
        dict_rows=True,
    )
    relationships = []
    for r in rels_rows:
        if str(r["entity_a_id"]) == str(entity_id):
            other_name, other_type, direction = r["name_b"], r["type_b"], "from"
        else:
            other_name, other_type, direction = r["name_a"], r["type_a"], "to"
        relationships.append({
            "other_entity_name": other_name,
            "other_entity_type": other_type,
            "direction": direction,
            "rel_type": r.get("rel_type"),
            "from_chapter": r.get("from_chapter"),
            "to_chapter": r.get("to_chapter"),
            "notes": r.get("notes"),
        })
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "entity_type": row["entity_type"],
        "description": None,
        "relationships": relationships,
    }


def get_entity_graph(novel_id: UUID, cap: int | None) -> dict[str, Any]:
    db = _get_db()
    effective_cap = _resolve_cap(db, novel_id, cap)

    if hasattr(db, "entities"):
        # In-memory (FakeDB) path
        char_by_entity_id = {c["entity_id"]: c for c in db.characters}
        loc_by_entity_id  = {l["entity_id"]: l for l in db.locations}
        obj_by_entity_id  = {o["entity_id"]: o for o in db.objects}
        fac_by_entity_id  = {f["entity_id"]: f for f in db.factions}

        def _native_id(entity_id: Any, entity_type: str) -> str:
            if entity_type == "character" and entity_id in char_by_entity_id:
                return str(char_by_entity_id[entity_id]["id"])
            if entity_type == "location" and entity_id in loc_by_entity_id:
                return str(loc_by_entity_id[entity_id]["id"])
            if entity_type == "object" and entity_id in obj_by_entity_id:
                return str(obj_by_entity_id[entity_id]["id"])
            if entity_type == "faction" and entity_id in fac_by_entity_id:
                return str(fac_by_entity_id[entity_id]["id"])
            return str(entity_id)

        nodes = []
        entity_id_set: set[Any] = set()
        for e in db.entities:
            if e.get("novel_id") != novel_id:
                continue
            if e.get("entity_type") == "character":
                char = char_by_entity_id.get(e["id"])
                if (
                    char
                    and char.get("first_appearance_chapter") is not None
                    and char["first_appearance_chapter"] > effective_cap
                ):
                    continue
            nodes.append({
                "id": str(e["id"]),
                "label": e["name"],
                "entity_type": e["entity_type"],
                "native_id": _native_id(e["id"], e["entity_type"]),
                "description": e.get("description"),
            })
            entity_id_set.add(e["id"])

        edges = [
            {
                "id": str(r["id"]),
                "from": str(r["entity_a_id"]),
                "to": str(r["entity_b_id"]),
                "label": r.get("rel_type"),
                "chapter_number": r.get("from_chapter"),
            }
            for r in db.relationships
            if r["entity_a_id"] in entity_id_set
            and r["entity_b_id"] in entity_id_set
            and (r.get("from_chapter") is None or r["from_chapter"] <= effective_cap)
        ]
        for e in edges:
            e["edge_kind"] = "relationship"
            e["tooltip"] = None

        chapter_by_id = {c["id"]: c for c in db.chapters}
        raw_story: list[dict] = []

        # shared_dynamics
        for sd in db.shared_dynamics:
            chap = chapter_by_id.get(sd.get("chapter_id"))
            if chap is None or chap.get("novel_id") != novel_id:
                continue
            if chap["number"] > effective_cap:
                continue
            if sd["entity_a_id"] not in entity_id_set or sd["entity_b_id"] not in entity_id_set:
                continue
            raw_story.append({
                "from": str(sd["entity_a_id"]),
                "to": str(sd["entity_b_id"]),
                "edge_kind": "dynamic",
                "description": sd.get("description"),
            })

        return {"nodes": nodes, "edges": edges}

    # Real DB path
    node_rows = db.fetchall(
        """
        SELECT e.id::text AS id,
               e.name AS label,
               e.entity_type,
               COALESCE(c.id, l.id, o.id, f.id, e.id)::text AS native_id,
               NULL::text AS description
        FROM entities e
        LEFT JOIN characters c ON c.entity_id = e.id
        LEFT JOIN locations  l ON l.entity_id = e.id
        LEFT JOIN objects    o ON o.entity_id = e.id
        LEFT JOIN factions   f ON f.entity_id = e.id
        WHERE e.novel_id = %s
          AND (
            e.entity_type != 'character'
            OR c.first_appearance_chapter IS NULL
            OR c.first_appearance_chapter <= %s
          )
        """,
        (str(novel_id), effective_cap),
        dict_rows=True,
    )
    nodes_list = [dict(r) for r in node_rows]

    edge_rows = db.fetchall(
        """
        SELECT r.id::text AS id,
               r.entity_a_id::text AS "from",
               r.entity_b_id::text AS "to",
               r.rel_type AS label,
               r.from_chapter AS chapter_number
        FROM relationships r
        JOIN entities ea ON ea.id = r.entity_a_id AND ea.novel_id = %s
        JOIN entities eb ON eb.id = r.entity_b_id AND eb.novel_id = %s
        WHERE r.from_chapter IS NULL OR r.from_chapter <= %s
        """,
        (str(novel_id), str(novel_id), effective_cap),
        dict_rows=True,
    )
    edges_list = [dict(r) for r in edge_rows]
    for e in edges_list:
        e["edge_kind"] = "relationship"
        e["tooltip"] = None
    raw_story: list[dict] = []

    dyn_rows = db.fetchall(
        """
        SELECT sd.entity_a_id::text AS "from",
               sd.entity_b_id::text AS "to",
               'dynamic'            AS edge_kind,
               sd.description       AS description
        FROM shared_dynamics sd
        JOIN chapters ch ON ch.id = sd.chapter_id
        JOIN entities ea ON ea.id = sd.entity_a_id AND ea.novel_id = %s
        JOIN entities eb ON eb.id = sd.entity_b_id AND eb.novel_id = %s
        WHERE ch.number <= %s
        """,
        (str(novel_id), str(novel_id), effective_cap),
        dict_rows=True,
    )
    raw_story.extend(dict(r) for r in dyn_rows)

    return {"nodes": nodes_list, "edges": edges_list}
