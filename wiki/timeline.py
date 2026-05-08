from __future__ import annotations

import argparse
import json
from typing import Any

from db.client import DBClient


def _resolve_character_id(db: DBClient, novel_id: str, character_name: str) -> str | None:
    row = db.fetchone(
        """
        SELECT id
        FROM characters
        WHERE novel_id = %s AND lower(name) = lower(%s)
        LIMIT 1
        """,
        (novel_id, character_name),
    )
    if row is None:
        return None
    return str(row[0])


def _resolve_location_id(db: DBClient, novel_id: str, location_name: str) -> str | None:
    row = db.fetchone(
        """
        SELECT id
        FROM locations
        WHERE novel_id = %s AND lower(name) = lower(%s)
        LIMIT 1
        """,
        (novel_id, location_name),
    )
    if row is None:
        return None
    return str(row[0])


def build_timeline(
    novel_id: str,
    *,
    up_to_chapter: int | None = None,
    character: str | None = None,
    location: str | None = None,
    event_type: str | None = None,
    impact: str | None = None,
) -> list[dict[str, Any]]:
    with DBClient() as db:
        where_clauses = ["ch.novel_id = %s"]
        params: list[Any] = [novel_id]

        if up_to_chapter is not None:
            where_clauses.append("ch.number <= %s")
            params.append(up_to_chapter)

        if event_type:
            where_clauses.append("e.event_type = %s")
            params.append(event_type)

        if impact:
            where_clauses.append("e.impact_level = %s")
            params.append(impact)

        if character:
            character_id = _resolve_character_id(db, novel_id, character)
            if character_id is None:
                return []
            where_clauses.append("%s::uuid = ANY(e.involved_characters)")
            params.append(character_id)

        if location:
            location_id = _resolve_location_id(db, novel_id, location)
            if location_id is None:
                return []
            where_clauses.append("%s::uuid = ANY(e.involved_locations)")
            params.append(location_id)

        where_sql = " AND ".join(where_clauses)

        rows = db.fetchall(
            f"""
            SELECT e.id,
                   e.description,
                   e.event_type,
                   e.impact_level,
                   e.involved_characters,
                   e.involved_locations,
                   e.involved_objects,
                   ch.id AS chapter_id,
                   ch.number AS chapter_number,
                   ch.title AS chapter_title
            FROM events e
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE {where_sql}
            ORDER BY ch.number ASC, e.created_at ASC
            """,
            tuple(params),
            dict_rows=True,
        )

        return [dict(row) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build timeline view")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--up-to-chapter", type=int)
    parser.add_argument("--character")
    parser.add_argument("--location")
    parser.add_argument("--event-type")
    parser.add_argument("--impact")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    timeline = build_timeline(
        args.novel_id,
        up_to_chapter=args.up_to_chapter,
        character=args.character,
        location=args.location,
        event_type=args.event_type,
        impact=args.impact,
    )

    if args.format == "json":
        print(json.dumps(timeline, indent=2, default=str))
        return

    lines = ["# Timeline", ""]
    for event in timeline:
        lines.append(
            f"- Chapter {event['chapter_number']}: {event['description']} "
            f"({event['event_type']}/{event['impact_level']})"
        )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
