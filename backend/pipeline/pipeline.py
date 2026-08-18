from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from pipeline.config import settings
from pipeline.critic.service import critique_chapter
from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, embed_chapter_and_events
from pipeline.extraction.canonicalizer import (
    EntityCanonicalizer,
    IntraExtractionDeduplicator,
    apply_merges_to_extraction,
    collect_names_by_type,
)
from pipeline.extraction.chunker import sliding_window_chunks
from pipeline.extraction.context_select import select_context_entities
from pipeline.extraction.extractor import ChapterExtractor
from pipeline.extraction.persist_canon import persist_canon_facts
from pipeline.extraction.persist_deltas import persist_state_deltas
from pipeline.extraction.persist_extras import (
    persist_commitments,
    persist_knows_edges,
    persist_multi_summaries,
    persist_scenes,
)
from pipeline.extraction.resolver import EntityResolver
from pipeline.style import compute_style_fingerprint
from pipeline.ingestion.ingest import delete_chapter_data, ingest_chapter
from pipeline.state.materializer import StateMaterializer

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _normalize_custom_entities(
    custom_entities: list[Any],
    custom_entity_types: list[dict],
) -> list[dict]:
    """Drop extracted custom entities whose type isn't registered for the novel
    and normalize the type to its registered casing. Unregistered types would
    otherwise create entities rows that no dedup pass or UI page ever sees."""
    registered = {
        str(t.get("name", "")).strip().lower(): str(t.get("name", "")).strip()
        for t in custom_entity_types or []
        if str(t.get("name", "")).strip()
    }
    normalized: list[dict] = []
    for item in custom_entities or []:
        if not isinstance(item, dict):
            continue
        raw_type = str(item.get("type", "")).strip()
        match = registered.get(raw_type.lower())
        if match is None:
            logger.warning(
                "custom entity %r has unregistered type %r — skipped",
                item.get("name"),
                raw_type,
            )
            continue
        item["type"] = match
        normalized.append(item)
    return normalized


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
            cur.execute("DELETE FROM schema_version")
            cur.execute(
                "INSERT INTO schema_version (version) VALUES (%s)",
                (SCHEMA_VERSION,),
            )
        _assert_embedding_dimension_matches(db)


def _assert_embedding_dimension_matches(db: DBClient) -> None:
    """Fail loudly when EMBEDDING_DIMENSIONS drifts from the live schema.

    Vector columns are created with the configured size baked in, but every
    CREATE TABLE is IF NOT EXISTS, so re-running init-db against a populated
    database silently leaves the old width in place. Every subsequent chapter
    then dies on `%s::vector` with an error that never mentions the config
    change that caused it.
    """
    live = db.fetchval(
        """
        SELECT atttypmod
        FROM pg_attribute
        WHERE attrelid = 'chapters'::regclass AND attname = 'embedding'
        """
    )
    if live is None:
        return
    if int(live) != settings.embedding_dimensions:
        raise SystemExit(
            f"EMBEDDING_DIMENSIONS is {settings.embedding_dimensions} but "
            f"chapters.embedding is vector({int(live)}). CREATE TABLE IF NOT "
            "EXISTS cannot widen an existing column: either restore the old "
            "value, or migrate the vector columns and re-embed."
        )


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


def load_story_context(
    db: DBClient,
    novel_id: str,
    chapter_number: int,
    custom_entity_types: list[dict] | None = None,
    chapter_text: str = "",
) -> dict[str, Any]:
    characters = db.fetchall(
        """
        SELECT c.id, c.name, c.aliases,
               ls.emotional_state, ls.goals, ls.physical_state, ls.last_chapter
        FROM characters c
        LEFT JOIN LATERAL (
            SELECT cs.emotional_state, cs.goals, cs.physical_state, ch.number AS last_chapter
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
        SELECT id, name, description, first_appearance_chapter
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

    custom_entities: dict[str, list[dict]] = {}
    for et in (custom_entity_types or []):
        type_name = et["name"]
        rows = db.fetchall(
            "SELECT name FROM entities WHERE novel_id = %s AND entity_type = %s ORDER BY name",
            (novel_id, type_name),
            dict_rows=True,
        )
        custom_entities[type_name] = [{"name": r["name"]} for r in rows]

    character_rows = [dict(row) for row in characters]
    location_rows = [dict(row) for row in locations]
    if chapter_text:
        # Mentioned-in-chapter entities first, recency backfill, capped — keeps
        # prompt size bounded as the cast grows.
        character_rows = select_context_entities(
            chapter_text, character_rows, cap=settings.context_max_characters
        )
        location_rows = select_context_entities(
            chapter_text, location_rows, cap=settings.context_max_locations
        )

    return {
        "characters": character_rows,
        "locations": location_rows,
        "open_threads": [dict(row) for row in open_threads],
        "recent_events": [dict(row) for row in recent_events],
        "custom_entities": custom_entities,
    }


def analyze_chapter(
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    chapter_title: str | None,
    use_mock_llm: bool | None,
    chunk_size: int,
    chunk_overlap: int,
    progress: Any | None = None,
    db: DBClient | None = None,
    replace: bool = False,
    source: str = "human",
    run_critic: bool | None = None,
) -> dict[str, Any]:
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        # Fail fast on duplicates before paying for LLM extraction.
        existing = client.fetchval(
            "SELECT id FROM chapters WHERE novel_id = %s AND number = %s",
            (novel_id, chapter_number),
        )
        if existing is not None and not replace:
            raise ValueError(
                f"Chapter {chapter_number} already exists for novel {novel_id}. "
                "Pass replace=True to re-process it."
            )

        custom_entity_types = [
            dict(r)
            for r in client.fetchall(
                "SELECT name, description FROM novel_entity_types WHERE novel_id = %s ORDER BY name",
                (novel_id,),
                dict_rows=True,
            )
        ]

        context = load_story_context(
            client,
            novel_id,
            chapter_number,
            custom_entity_types=custom_entity_types,
            chapter_text=raw_text,
        )
        chunks = sliding_window_chunks(raw_text, chunk_size=chunk_size, overlap=chunk_overlap)
        extractor = ChapterExtractor(use_mock=use_mock_llm)
        extracted = extractor.extract_chapter(
            chunks=chunks,
            context=context,
            progress=progress,
            custom_entity_types=custom_entity_types or None,
        )
        extracted["custom_entities"] = _normalize_custom_entities(
            extracted.get("custom_entities", []), custom_entity_types
        )

        if progress is not None:
            progress.on_pass_start("intra_dedup")
        deduplicator = IntraExtractionDeduplicator(use_mock=use_mock_llm)
        extracted = deduplicator.deduplicate(extracted, raw_text)
        if progress is not None:
            progress.on_pass_done("intra_dedup")

        # Canonicalizer alias writes are intentionally OUTSIDE the transaction
        # below: they're additive metadata, harmless if persistence later fails,
        # and re-processing resolves onto them.
        if progress is not None:
            progress.on_pass_start("canonicalization")
        canonicalizer = EntityCanonicalizer(client, novel_id=novel_id, use_mock=use_mock_llm)
        merges = canonicalizer.canonicalize(
            chapter_text=raw_text,
            candidate_names_by_type=collect_names_by_type(extracted),
        )
        # Rewrite merged candidates to their targets' canonical names so
        # every merge takes effect this chapter — for reasoning-only
        # (non-persisted-alias) merges this rename is the only mechanism.
        extracted = apply_merges_to_extraction(client, extracted, merges)
        if progress is not None:
            progress.on_pass_done("canonicalization")

        # ---- everything below is one transaction ----
        # The session pins one pooled connection across the embedding network
        # calls below; bounded today by jobs.py max_workers=2 vs pool max_size=5.
        with client.session() as s:
            if replace:
                delete_chapter_data(s, novel_id=novel_id, chapter_number=chapter_number)
            chapter_id = ingest_chapter(
                s,
                novel_id=novel_id,
                chapter_number=chapter_number,
                title=chapter_title,
                raw_text=raw_text,
                source=source,
            )
            resolver = EntityResolver(s, novel_id=novel_id, chapter_number=chapter_number)
            event_rows = _persist_extraction(
                s,
                resolver=resolver,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                extracted=extracted,
            )

            embedding_service = EmbeddingService(use_mock=use_mock_llm)
            embed_chapter_and_events(
                s,
                chapter_id=chapter_id,
                chapter_summary=extracted.get("summary", ""),
                event_rows=event_rows,
                service=embedding_service,
                # persist_multi_summaries re-embeds the chapter from summary_medium;
                # skip the throwaway embedding when that will happen.
                embed_chapter=not (extracted.get("summary_medium") or "").strip(),
            )

            s.execute(
                """
                UPDATE chapters
                SET summary = %s,
                    processed_at = now()
                WHERE id = %s
                """,
                (extracted.get("summary", ""), chapter_id),
            )
            s.execute(
                "UPDATE chapters SET style_fingerprint = %s::jsonb WHERE id = %s",
                (json.dumps(compute_style_fingerprint(raw_text)), chapter_id),
            )

            persist_multi_summaries(
                s,
                chapter_id=chapter_id,
                summary_short=extracted.get("summary_short", ""),
                summary_medium=extracted.get("summary_medium", ""),
                summary_long=extracted.get("summary_long", ""),
                embedder=embedding_service,
            )
            persist_scenes(
                s,
                chapter_id=chapter_id,
                scenes_data=extracted.get("scenes", []),
                resolver=resolver,
                embedder=embedding_service,
            )
            persist_knows_edges(
                s,
                chapter_number=chapter_number,
                learnings=extracted.get("learnings", []),
                resolver=resolver,
            )
            persist_commitments(
                s,
                novel_id=novel_id,
                chapter_number=chapter_number,
                foreshadows=extracted.get("foreshadows_introduced", []),
                payoffs=extracted.get("payoffs_delivered", []),
                resolver=resolver,
                embedder=embedding_service,
            )
            persist_canon_facts(
                s,
                novel_id=novel_id,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                facts=extracted.get("canon_facts", []),
                resolver=resolver,
            )
            persist_state_deltas(
                s,
                chapter_id=chapter_id,
                deltas=extracted.get("state_deltas", []),
                resolver=resolver,
            )

        # ---- phase 4: MATERIALIZE / phase 5: CRITIQUE ----
        # Both derive from the committed data and are idempotent; a failure
        # here must not roll back the saved chapter. materialized/critique in
        # the result tell callers whether a manual re-run is needed.
        materialized = False
        critique_summary: dict[str, Any] | None = None
        try:
            max_chapter = client.fetchval(
                "SELECT COALESCE(MAX(number), %s) FROM chapters WHERE novel_id = %s",
                (chapter_number, novel_id),
            )
            StateMaterializer(client).materialize(novel_id, int(max_chapter))
            materialized = True
        except Exception:
            logger.exception("materialize failed for novel %s; re-run pipeline.state.cli", novel_id)
        # The critic is optional and fully decoupled: it reads only committed
        # data, so skipping it here costs nothing that
        # `python -m pipeline.critic.cli` cannot supply later.
        want_critic = settings.critic_enabled if run_critic is None else run_critic
        if want_critic:
            try:
                critique_summary = critique_chapter(
                    client,
                    novel_id=novel_id,
                    chapter_number=chapter_number,
                    chapter_id=chapter_id,
                    raw_text=raw_text,
                    extracted=extracted,
                    use_mock_llm=use_mock_llm,
                )
            except Exception:
                logger.exception(
                    "critique failed for chapter %s of novel %s; re-run "
                    "pipeline.critic.cli", chapter_number, novel_id,
                )

        return {
            "chapter_id": chapter_id,
            "chunks": len(chunks),
            "summary_preview": extracted.get("summary", "")[:200],
            "new_characters": len(extracted.get("new_entities", {}).get("characters", [])),
            "new_locations": len(extracted.get("new_entities", {}).get("locations", [])),
            "events": len(extracted.get("events", [])),
            "state_deltas": len(extracted.get("state_deltas", [])),
            "thread_updates": len(extracted.get("thread_updates", [])),
            "continuity_flags": len(extracted.get("continuity_flags", [])),
            "materialized": materialized,
            "critique": critique_summary,
        }
    finally:
        if owned:
            client.close()


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

    for custom_entity in extracted.get("custom_entities", []):
        name = str(custom_entity.get("name", "")).strip()
        entity_type = str(custom_entity.get("type", "")).strip()
        if not name or not entity_type:
            continue
        resolver.resolve_custom_entity(name, entity_type, custom_entity)

    for rel in extracted.get("relationship_updates", []):
        a_name = str(rel.get("entity_a", "")).strip()
        b_name = str(rel.get("entity_b", "")).strip()
        if not a_name or not b_name:
            continue
        a_universal = resolver.resolve_any_entity(a_name, create=False)
        b_universal = resolver.resolve_any_entity(b_name, create=False)
        if a_universal is None or b_universal is None:
            # One endpoint doesn't correspond to any known entity — dropping the
            # edge is correct rather than minting a phantom character for it.
            continue
        if a_universal == b_universal:
            logger.warning(
                "relationship_updates: entity_a %r and entity_b %r both resolved to the "
                "same entity; skipping self-referential relationship", a_name, b_name,
            )
            continue
        # Identical active relationship already recorded -> don't re-insert.
        # Different rel_types between the same pair coexist by design.
        duplicate = db.fetchone(
            """
            SELECT id FROM relationships
             WHERE rel_type IS NOT DISTINCT FROM %s
               AND superseded_by_id IS NULL
               AND (
                     (entity_a_id = %s AND entity_b_id = %s)
                  OR (entity_a_id = %s AND entity_b_id = %s)
               )
             LIMIT 1
            """,
            (rel.get("rel_type"), a_universal, b_universal, b_universal, a_universal),
        )
        if duplicate:
            continue
        symmetric = rel.get("symmetric")
        if not isinstance(symmetric, bool):
            symmetric = None
        db.execute(
            """
            INSERT INTO relationships (
                entity_a_id, entity_b_id, rel_type, "symmetric", from_chapter, to_chapter, notes, chapter_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                a_universal,
                b_universal,
                rel.get("rel_type"),
                symmetric,
                rel.get("from_chapter"),
                rel.get("to_chapter"),
                rel.get("notes"),
                chapter_id,
            ),
        )

    # The extractor may emit several dynamics for the same pair in one chapter
    # (including with entity_a/entity_b swapped); UNIQUE(entity_a_id,
    # entity_b_id, chapter_id) allows only one row, so collapse them first.
    dynamics_by_pair: dict[frozenset[str], dict] = {}
    for dyn in extracted.get("dynamics_updates", []):
        a_name = str(dyn.get("entity_a", "")).strip()
        b_name = str(dyn.get("entity_b", "")).strip()
        description = str(dyn.get("description", "")).strip()
        if not a_name or not b_name or not description:
            continue
        a_universal = resolver.resolve_any_entity(a_name, create=False)
        b_universal = resolver.resolve_any_entity(b_name, create=False)
        if a_universal is None or b_universal is None:
            continue
        if a_universal == b_universal:
            logger.warning(
                "dynamics_updates: entity_a %r and entity_b %r both resolved to the "
                "same entity; skipping self-referential dynamic", a_name, b_name,
            )
            continue
        pair = frozenset((str(a_universal), str(b_universal)))
        entry = dynamics_by_pair.get(pair)
        if entry is None:
            dynamics_by_pair[pair] = {
                "a": a_universal, "b": b_universal, "descriptions": [description],
            }
        elif description not in entry["descriptions"]:
            entry["descriptions"].append(description)
    for entry in dynamics_by_pair.values():
        db.execute(
            """
            INSERT INTO shared_dynamics (entity_a_id, entity_b_id, chapter_id, description)
            VALUES (%s, %s, %s, %s)
            """,
            (entry["a"], entry["b"], chapter_id, " ".join(entry["descriptions"])),
        )

    inserted_events: list[dict[str, str]] = []
    for event in extracted.get("events", []):
        description = str(event.get("description", "")).strip()
        if not description:
            continue

        # Reference-only: an event can only involve characters that already
        # exist. Resolve-or-drop so a game-system noun named as an actor doesn't
        # mint a phantom character row.
        involved_characters = [
            resolved.entity_id
            for name in event.get("involved_characters", [])
            if str(name).strip()
            for resolved in (resolver.resolve_character(name, create=False),)
            if resolved is not None
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
        involved_factions = [
            resolver.resolve_faction(name).entity_id
            for name in event.get("involved_factions", [])
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
                involved_objects,
                involved_factions
            )
            VALUES (%s, %s, %s, %s, %s::uuid[], %s::uuid[], %s::uuid[], %s::uuid[])
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
                involved_factions,
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
                    ELSE NULL
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
    process_parser.add_argument(
        "--replace", action="store_true",
        help="Delete this chapter's previously extracted data and re-process it",
    )

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

        result = analyze_chapter(
            novel_id=args.novel_id,
            chapter_number=args.number,
            raw_text=chapter_text,
            chapter_title=args.title,
            use_mock_llm=True if args.mock_llm else None,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            replace=args.replace,
        )
        print(json.dumps(result, indent=2))
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
