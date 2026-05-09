from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pipeline.config import settings
from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, embed_chapter_and_events
from pipeline.extraction.canonicalizer import CharacterCanonicalizer, collect_character_names
from pipeline.extraction.chunker import sliding_window_chunks
from pipeline.extraction.extractor import ChapterExtractor
from pipeline.extraction.resolver import EntityResolver
from pipeline.ingestion.ingest import ingest_chapter


def _read_chapter_text(file_path: str | None) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")

    if not sys.stdin.isatty():
        return sys.stdin.read()

    print("Paste chapter text. Submit EOF (Ctrl-D) when done:")
    lines: list[str] = []
    try:
        while True:
            lines.append(input())
    except EOFError:
        pass
    return "\n".join(lines)


def init_db(schema_path: str | None = None) -> None:
    if schema_path is None:
        schema_path = str(Path(__file__).parent / "db" / "schema.sql")
    sql = Path(schema_path).read_text(encoding="utf-8")
    sql = sql.replace("__EMBEDDING_DIM__", str(settings.embedding_dimensions))
    with DBClient() as db:
        with db.cursor(commit=True) as cur:
            cur.execute(sql)


def create_novel(title: str, author: str | None, language: str) -> str:
    with DBClient() as db:
        novel_id = db.fetchval(
            """
            INSERT INTO novels (title, author, language)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (title, author, language),
            commit=True,
        )
        return str(novel_id)


def list_novels() -> list[dict[str, Any]]:
    with DBClient() as db:
        rows = db.fetchall(
            """
            SELECT id, title, author, language, created_at
            FROM novels
            ORDER BY created_at DESC
            """,
            dict_rows=True,
        )
        return [dict(row) for row in rows]


def load_story_context(db: DBClient, novel_id: str, chapter_number: int) -> dict[str, Any]:
    characters = db.fetchall(
        """
        SELECT c.id, c.name, c.aliases,
               ls.emotional_state, ls.goals, ls.physical_state
        FROM characters c
        LEFT JOIN LATERAL (
            SELECT cs.emotional_state, cs.goals, cs.physical_state
            FROM character_states cs
            JOIN chapters ch ON ch.id = cs.chapter_id
            WHERE cs.character_id = c.id
              AND ch.number < %s
            ORDER BY ch.number DESC
            LIMIT 1
        ) ls ON true
        WHERE c.novel_id = %s
        ORDER BY c.name
        """,
        (chapter_number, novel_id),
        dict_rows=True,
    )

    locations = db.fetchall(
        """
        SELECT id, name, description
        FROM locations
        WHERE novel_id = %s
        ORDER BY name
        """,
        (novel_id,),
        dict_rows=True,
    )

    open_threads = db.fetchall(
        """
        SELECT id, title, description, status, thread_type, opened_chapter, closed_chapter
        FROM plot_threads
        WHERE novel_id = %s AND status <> 'closed'
        ORDER BY opened_chapter NULLS LAST, title
        """,
        (novel_id,),
        dict_rows=True,
    )

    recent_events = db.fetchall(
        """
        WITH max_number AS (
            SELECT COALESCE(MAX(number), 0) AS value
            FROM chapters
            WHERE novel_id = %s
              AND number < %s
        )
        SELECT e.id, e.description, e.event_type, e.impact_level, ch.number AS chapter_number
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        CROSS JOIN max_number
        WHERE ch.novel_id = %s
          AND ch.number BETWEEN GREATEST(max_number.value - 2, 1) AND max_number.value
        ORDER BY ch.number DESC, e.created_at DESC
        LIMIT 200
        """,
        (novel_id, chapter_number, novel_id),
        dict_rows=True,
    )

    return {
        "characters": [dict(row) for row in characters],
        "locations": [dict(row) for row in locations],
        "open_threads": [dict(row) for row in open_threads],
        "recent_events": [dict(row) for row in recent_events],
    }


def process_chapter(
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    chapter_title: str | None,
    use_mock_llm: bool | None,
    chunk_size: int,
    chunk_overlap: int,
    progress: Any | None = None,
) -> dict[str, Any]:
    with DBClient() as db:
        chapter_id = ingest_chapter(
            db,
            novel_id=novel_id,
            chapter_number=chapter_number,
            title=chapter_title,
            raw_text=raw_text,
        )

        context = load_story_context(db, novel_id, chapter_number)
        chunks = sliding_window_chunks(raw_text, chunk_size=chunk_size, overlap=chunk_overlap)
        extractor = ChapterExtractor(use_mock=use_mock_llm)
        extracted = extractor.extract_chapter(chunks=chunks, context=context, progress=progress)

        if progress is not None:
            progress.on_pass_start("canonicalization")
        canonicalizer = CharacterCanonicalizer(db, novel_id=novel_id, use_mock=use_mock_llm)
        canonicalizer.canonicalize(
            chapter_text=raw_text,
            candidate_names=collect_character_names(extracted),
        )
        if progress is not None:
            progress.on_pass_done("canonicalization")

        resolver = EntityResolver(db, novel_id=novel_id, chapter_number=chapter_number)
        event_rows = _persist_extraction(
            db,
            resolver=resolver,
            chapter_id=chapter_id,
            chapter_number=chapter_number,
            extracted=extracted,
        )

        embedding_service = EmbeddingService(use_mock=use_mock_llm)
        embed_chapter_and_events(
            db,
            chapter_id=chapter_id,
            chapter_summary=extracted.get("summary", ""),
            event_rows=event_rows,
            service=embedding_service,
        )

        db.execute(
            """
            UPDATE chapters
            SET summary = %s,
                processed_at = now()
            WHERE id = %s
            """,
            (extracted.get("summary", ""), chapter_id),
        )

        return {
            "chapter_id": chapter_id,
            "chunks": len(chunks),
            "summary_preview": extracted.get("summary", "")[:200],
            "new_characters": len(extracted.get("new_entities", {}).get("characters", [])),
            "new_locations": len(extracted.get("new_entities", {}).get("locations", [])),
            "events": len(extracted.get("events", [])),
            "thread_updates": len(extracted.get("thread_updates", [])),
            "continuity_flags": len(extracted.get("continuity_flags", [])),
        }


def _persist_extraction(
    db: DBClient,
    *,
    resolver: EntityResolver,
    chapter_id: str,
    chapter_number: int,
    extracted: dict[str, Any],
) -> list[dict[str, str]]:
    new_entities = extracted.get("new_entities", {})

    for character in new_entities.get("characters", []):
        name = str(character.get("name", "")).strip()
        if not name:
            continue
        resolver.resolve_character(name, character)

    for location in new_entities.get("locations", []):
        name = str(location.get("name", "")).strip()
        if not name:
            continue
        resolver.resolve_location(name, location)

    for faction in new_entities.get("factions", []):
        name = str(faction.get("name", "")).strip()
        if not name:
            continue
        resolver.resolve_faction(name, faction)

    for obj in new_entities.get("objects", []):
        name = str(obj.get("name", "")).strip()
        if not name:
            continue
        resolver.resolve_object(name, obj)

    for delta in extracted.get("entity_deltas", []):
        character_name = str(delta.get("character_name", "")).strip()
        if not character_name:
            continue
        character_id = resolver.resolve_character(character_name).entity_id

        location_name = str(delta.get("location", "")).strip()
        location_id = None
        if location_name:
            location_id = resolver.resolve_location(location_name).entity_id

        knowledge = delta.get("knowledge")
        if not isinstance(knowledge, list):
            knowledge = []
        knowledge = [str(item) for item in knowledge if str(item).strip()]

        relationships = delta.get("relationships")
        if not isinstance(relationships, dict):
            relationships = {}

        db.execute(
            """
            INSERT INTO character_states (
                character_id,
                chapter_id,
                location_id,
                emotional_state,
                goals,
                knowledge,
                relationships,
                physical_state,
                notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                character_id,
                chapter_id,
                location_id,
                delta.get("emotional_state"),
                delta.get("goals"),
                knowledge,
                json.dumps(relationships),
                delta.get("physical_state"),
                delta.get("notes"),
            ),
        )

        for target_name, rel_type in relationships.items():
            clean_target = str(target_name).strip()
            if not clean_target:
                continue
            target_id = resolver.resolve_character(clean_target).entity_id
            db.execute(
                """
                INSERT INTO relationships (
                    entity_a_id,
                    entity_a_type,
                    entity_b_id,
                    entity_b_type,
                    rel_type,
                    status,
                    chapter_id,
                    notes
                )
                VALUES (%s, 'character', %s, 'character', %s, 'active', %s, %s)
                """,
                (
                    character_id,
                    target_id,
                    str(rel_type),
                    chapter_id,
                    f"Derived from character delta for chapter {chapter_number}.",
                ),
            )

    inserted_events: list[dict[str, str]] = []
    for event in extracted.get("events", []):
        description = str(event.get("description", "")).strip()
        if not description:
            continue

        involved_characters = [
            resolver.resolve_character(name).entity_id
            for name in event.get("involved_characters", [])
            if str(name).strip()
        ]
        involved_locations = [
            resolver.resolve_location(name).entity_id
            for name in event.get("involved_locations", [])
            if str(name).strip()
        ]
        involved_objects = [
            resolver.resolve_object(name).entity_id
            for name in event.get("involved_objects", [])
            if str(name).strip()
        ]

        event_id = db.fetchval(
            """
            INSERT INTO events (
                chapter_id,
                description,
                event_type,
                impact_level,
                involved_characters,
                involved_locations,
                involved_objects
            )
            VALUES (%s, %s, %s, %s, %s::uuid[], %s::uuid[], %s::uuid[])
            RETURNING id
            """,
            (
                chapter_id,
                description,
                event.get("event_type") or "other",
                event.get("impact_level") or "medium",
                involved_characters,
                involved_locations,
                involved_objects,
            ),
            commit=True,
        )
        inserted_events.append({"id": str(event_id), "description": description})

    event_lookup = {row["description"].lower(): row["id"] for row in inserted_events}

    for update in extracted.get("thread_updates", []):
        title = str(update.get("title", "")).strip()
        if not title:
            continue

        status = str(update.get("status", "progressing")).strip().lower() or "progressing"
        if status not in {"open", "progressing", "closed"}:
            status = "progressing"

        thread_id = db.fetchval(
            """
            INSERT INTO plot_threads (
                novel_id,
                title,
                description,
                status,
                opened_chapter,
                closed_chapter,
                thread_type
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                CASE WHEN %s = 'closed' THEN %s ELSE NULL END,
                %s
            )
            ON CONFLICT (novel_id, title)
            DO UPDATE SET
                description = COALESCE(EXCLUDED.description, plot_threads.description),
                status = EXCLUDED.status,
                thread_type = COALESCE(EXCLUDED.thread_type, plot_threads.thread_type),
                closed_chapter = CASE
                    WHEN EXCLUDED.status = 'closed' THEN EXCLUDED.closed_chapter
                    ELSE plot_threads.closed_chapter
                END
            RETURNING id
            """,
            (
                resolver.novel_id,
                title,
                update.get("description"),
                status,
                chapter_number,
                status,
                chapter_number,
                update.get("thread_type") or "other",
            ),
            commit=True,
        )

        impact = str(update.get("impact", "advances")).strip().lower() or "advances"
        if impact not in {"opens", "advances", "closes"}:
            impact = "advances"

        event_id = _find_related_event_id(
            event_lookup=event_lookup,
            event_rows=inserted_events,
            event_description=str(update.get("event_description", "")).strip(),
        )
        if event_id:
            db.execute(
                """
                INSERT INTO thread_events (thread_id, event_id, impact)
                VALUES (%s, %s, %s)
                ON CONFLICT (thread_id, event_id)
                DO UPDATE SET impact = EXCLUDED.impact
                """,
                (thread_id, event_id, impact),
            )

    for flag in extracted.get("continuity_flags", []):
        description = str(flag.get("description", "")).strip()
        if not description:
            continue
        db.execute(
            """
            INSERT INTO continuity_flags (chapter_id, description, flag_type)
            VALUES (%s, %s, %s)
            """,
            (chapter_id, description, flag.get("flag_type") or "other"),
        )

    return inserted_events


def _find_related_event_id(
    *,
    event_lookup: dict[str, str],
    event_rows: list[dict[str, str]],
    event_description: str,
) -> str | None:
    if not event_rows:
        return None

    if event_description:
        direct = event_lookup.get(event_description.lower())
        if direct:
            return direct

        lowered = event_description.lower()
        for row in event_rows:
            candidate = row["description"].lower()
            if lowered in candidate or candidate in lowered:
                return row["id"]

    return event_rows[0]["id"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Novel knowledge pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db_parser = subparsers.add_parser("init-db", help="Initialize database schema")
    init_db_parser.add_argument("--schema", default=None, help="Path to schema.sql (defaults to bundled)")

    create_novel_parser = subparsers.add_parser("create-novel", help="Create a novel record")
    create_novel_parser.add_argument("--title", required=True)
    create_novel_parser.add_argument("--author")
    create_novel_parser.add_argument("--language", default="en")

    subparsers.add_parser("list-novels", help="List novels")

    process_parser = subparsers.add_parser("process-chapter", help="Process one chapter")
    process_parser.add_argument("--novel-id", required=True)
    process_parser.add_argument("--number", required=True, type=int)
    process_parser.add_argument("--title")
    process_parser.add_argument("--file", help="Path to chapter text file")
    process_parser.add_argument("--mock-llm", action="store_true", help="Use deterministic mock extraction")
    process_parser.add_argument("--chunk-size", type=int, default=settings.chunk_size)
    process_parser.add_argument("--chunk-overlap", type=int, default=settings.chunk_overlap)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-db":
        init_db(args.schema)
        print(json.dumps({"status": "ok", "schema": args.schema}, indent=2))
        return

    if args.command == "create-novel":
        novel_id = create_novel(args.title, args.author, args.language)
        print(json.dumps({"novel_id": novel_id, "title": args.title}, indent=2))
        return

    if args.command == "list-novels":
        print(json.dumps(list_novels(), indent=2, default=str))
        return

    if args.command == "process-chapter":
        chapter_text = _read_chapter_text(args.file)
        if not chapter_text.strip():
            raise SystemExit("Chapter text is empty.")

        result = process_chapter(
            novel_id=args.novel_id,
            chapter_number=args.number,
            raw_text=chapter_text,
            chapter_title=args.title,
            use_mock_llm=True if args.mock_llm else None,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
        print(json.dumps(result, indent=2))
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
