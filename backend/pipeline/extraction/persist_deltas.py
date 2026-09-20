"""Persist typed state deltas — the tier-2 state log the materializer replays.

Reference-only resolution throughout: a delta naming an unknown entity is
dropped (with a warning), never minted. subject_id/object_id are universal
entity ids; location_id is the typed locations.id (matching the FK).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def persist_state_deltas(
    db: Any, *, chapter_id: str, deltas: list[dict], resolver: Any
) -> int:
    written = 0
    for delta in deltas or []:
        if not isinstance(delta, dict):
            continue
        kind = str(delta.get("kind", "")).strip().lower()
        character_name = str(delta.get("character_name", "")).strip()
        if not kind or not character_name:
            continue

        object_id = location_id = None
        attribute = detail = None
        change = str(delta.get("change", "")).strip().lower() or None

        if kind == "location":
            # A location delta's mover can be a character or a significant
            # object (prompts.py: "a character (or significant object)") — a
            # dagger moving between locations, say. resolve_character would
            # silently drop every object mover, so resolve_any_entity (which
            # checks the entities table and every typed table by name/alias)
            # is used instead. It returns a universal id STRING, not a
            # ResolvedEntity, unlike the other resolve_* calls below.
            subject_universal_id = resolver.resolve_any_entity(character_name, create=False)
            if subject_universal_id is None:
                logger.warning("state_delta: unknown mover %r — dropped", character_name)
                continue
            resolved_loc = resolver.resolve_location(
                str(delta.get("location_name", "")).strip(), create=False
            ) if str(delta.get("location_name", "")).strip() else None
            if resolved_loc is None:
                logger.warning("state_delta: unresolvable location %r — dropped", delta)
                continue
            location_id = resolved_loc.entity_id
            change = "move"
        else:
            subject = resolver.resolve_character(character_name, create=False)
            if subject is None:
                logger.warning("state_delta: unknown character %r — dropped", character_name)
                continue
            subject_universal_id = subject.universal_id

            if kind == "possession":
                resolved_obj = resolver.resolve_object(
                    str(delta.get("object_name", "")).strip(), create=False
                ) if str(delta.get("object_name", "")).strip() else None
                if resolved_obj is None or change not in {"gain", "loss"}:
                    logger.warning("state_delta: unresolvable possession %r — dropped", delta)
                    continue
                object_id = resolved_obj.universal_id
            elif kind == "status":
                attribute = str(delta.get("attribute", "")).strip()
                detail = str(delta.get("value", "")).strip()
                if attribute not in {"emotional_state", "goals", "physical_state", "appearance", "notes"} or not detail:
                    continue
                change = "update"
            else:
                continue

        db.execute(
            """
            INSERT INTO state_deltas (
                chapter_id, ordinal, kind, subject_id, object_id, location_id,
                change, attribute, detail, certainty
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                chapter_id, written, kind, subject_universal_id, object_id,
                location_id, change, attribute, detail,
                float(delta.get("certainty") or 1.0),
            ),
        )
        written += 1
    return written


__all__ = ["persist_state_deltas"]
