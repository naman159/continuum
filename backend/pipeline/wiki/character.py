from __future__ import annotations

import argparse
import json
from typing import Any

from pipeline.db.client import DBClient


def build_character_page(novel_id: str, name: str, up_to_chapter: int | None = None) -> dict[str, Any]:
    with DBClient() as db:
        character = db.fetchone(
            """
            SELECT id, name, aliases, first_appearance_chapter, description
            FROM characters
            WHERE novel_id = %s AND lower(name) = lower(%s)
            LIMIT 1
            """,
            (novel_id, name),
            dict_rows=True,
        )
        if character is None:
            raise ValueError(f"Character not found: {name}")

        character_id = str(character["id"])

        chapter_filter = ""
        params: list[Any] = [character_id]
        if up_to_chapter is not None:
            chapter_filter = " AND ch.number <= %s"
            params.append(up_to_chapter)

        latest_state = db.fetchone(
            f"""
            SELECT ch.number AS chapter_number,
                   l.name AS location,
                   cs.emotional_state,
                   cs.goals,
                   cs.knowledge,
                   cs.relationships,
                   cs.physical_state,
                   cs.notes
            FROM character_states cs
            JOIN chapters ch ON ch.id = cs.chapter_id
            LEFT JOIN locations l ON l.id = cs.location_id
            WHERE cs.character_id = %s
            {chapter_filter}
            ORDER BY ch.number DESC
            LIMIT 1
            """,
            tuple(params),
            dict_rows=True,
        )

        history = db.fetchall(
            f"""
            SELECT ch.number AS chapter_number,
                   l.name AS location,
                   cs.emotional_state,
                   cs.goals,
                   cs.knowledge,
                   cs.relationships,
                   cs.physical_state,
                   cs.notes
            FROM character_states cs
            JOIN chapters ch ON ch.id = cs.chapter_id
            LEFT JOIN locations l ON l.id = cs.location_id
            WHERE cs.character_id = %s
            {chapter_filter}
            ORDER BY ch.number ASC
            """,
            tuple(params),
            dict_rows=True,
        )

        events_params: list[Any] = [character_id]
        events_filter = ""
        if up_to_chapter is not None:
            events_filter = " AND ch.number <= %s"
            events_params.append(up_to_chapter)

        events = db.fetchall(
            f"""
            SELECT e.id,
                   ch.number AS chapter_number,
                   e.description,
                   e.event_type,
                   e.impact_level
            FROM events e
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE %s::uuid = ANY(e.involved_characters)
            {events_filter}
            ORDER BY ch.number, e.created_at
            """,
            tuple(events_params),
            dict_rows=True,
        )

        rel_params: list[Any] = [character_id, character_id]
        rel_filter = ""
        if up_to_chapter is not None:
            rel_filter = " AND ch.number <= %s"
            rel_params.append(up_to_chapter)

        relationships = db.fetchall(
            f"""
            SELECT r.entity_a_id,
                   r.entity_b_id,
                   r.entity_a_type,
                   r.entity_b_type,
                   r.rel_type,
                   r.status,
                   r.notes,
                   ch.number AS chapter_number,
                   ca.name AS entity_a_name,
                   cb.name AS entity_b_name
            FROM relationships r
            LEFT JOIN chapters ch ON ch.id = r.chapter_id
            LEFT JOIN characters ca ON ca.id = r.entity_a_id
            LEFT JOIN characters cb ON cb.id = r.entity_b_id
            WHERE (r.entity_a_id = %s OR r.entity_b_id = %s)
            {rel_filter}
            ORDER BY ch.number NULLS LAST, r.created_at
            """,
            tuple(rel_params),
            dict_rows=True,
        )

        return {
            "identity": {
                "id": character_id,
                "name": character["name"],
                "aliases": character["aliases"] or [],
                "first_appearance_chapter": character["first_appearance_chapter"],
                "description": character["description"],
            },
            "current_state": dict(latest_state) if latest_state else None,
            "history": [dict(row) for row in history],
            "relationships": [dict(row) for row in relationships],
            "events": [dict(row) for row in events],
            "spoiler_cap": up_to_chapter,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build character wiki page")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--up-to-chapter", type=int)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    payload = build_character_page(args.novel_id, args.name, args.up_to_chapter)

    if args.format == "json":
        print(json.dumps(payload, indent=2, default=str))
        return

    identity = payload["identity"]
    lines = [
        f"# {identity['name']}",
        "",
        f"- Aliases: {', '.join(identity['aliases']) if identity['aliases'] else 'None'}",
        f"- First appearance: Chapter {identity['first_appearance_chapter']}",
        f"- Description: {identity['description'] or 'N/A'}",
        "",
        "## Current State",
        json.dumps(payload["current_state"], indent=2, default=str),
        "",
        "## Event Count",
        str(len(payload["events"])),
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
