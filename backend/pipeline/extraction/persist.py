"""Persist the extraction tier inside the caller's chapter transaction."""

from __future__ import annotations

import logging
from typing import Any

from pipeline.db.client import DBClient
from pipeline.extraction.resolver import EntityResolver

logger = logging.getLogger(__name__)


def persist_extraction(
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
        symmetric = rel.get("symmetric")
        if not isinstance(symmetric, bool):
            symmetric = None
        # Direction matters: A mentors B and B mentors A are different facts.
        # Only explicitly mutual relationships may deduplicate a reversed pair.
        duplicate = db.fetchone(
            """
            SELECT id FROM relationships
             WHERE rel_type IS NOT DISTINCT FROM %s
               AND superseded_by_id IS NULL
               AND (from_chapter IS NULL OR from_chapter <= %s)
               AND (to_chapter IS NULL OR to_chapter >= %s)
               AND (
                     (entity_a_id = %s AND entity_b_id = %s)
                  OR (%s AND "symmetric" IS TRUE AND entity_a_id = %s AND entity_b_id = %s)
               )
             LIMIT 1
            """,
            (rel.get("rel_type"), chapter_number, chapter_number,
             a_universal, b_universal, symmetric is True, b_universal, a_universal),
        )
        if duplicate:
            continue
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

    # No match: return None rather than falling back to the chapter's first
    # event. That fallback welded every unidentifiable thread update to
    # whatever event happened to be extracted first, and thread_events rows are
    # read as evidence that a thread was advanced — so it invented links the
    # text never supported, and surfaced a blade-sharpening as the evidence for
    # a succession crisis. An unlinked thread update is merely incomplete;
    # a wrongly-linked one is false.
    return None
