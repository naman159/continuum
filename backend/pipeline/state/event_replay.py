from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any

from pipeline.db.client import DBClient
from pipeline.state.types import LocationFact, PossessionFact, StateSnapshot

logger = logging.getLogger(__name__)


# Simple verb-based possession heuristics. Order matters: most specific first.
GAIN_VERBS = (
    "took",
    "takes",
    "take",
    "received",
    "receives",
    "receive",
    "stole",
    "steals",
    "steal",
    "picked up",
    "picks up",
    "pick up",
    "grabbed",
    "grabs",
    "grab",
    "seized",
    "seizes",
    "seize",
    "acquired",
    "acquires",
    "acquire",
    "obtained",
    "obtains",
    "obtain",
    "found",
    "finds",
    "find",
    "claimed",
    "claims",
    "claim",
)

LOSS_VERBS = (
    "gave",
    "gives",
    "give",
    "lost",
    "loses",
    "lose",
    "dropped",
    "drops",
    "drop",
    "handed over",
    "hands over",
    "hand over",
    "handed",
    "hands",
    "hand",
    "discarded",
    "discards",
    "discard",
    "surrendered",
    "surrenders",
    "surrender",
    "relinquished",
    "relinquishes",
    "relinquish",
)


EMOTIONAL_HINTS = {
    "death": "grieving",
    "betrayal": "betrayed",
    "revelation": "shocked",
    "battle": "tense",
    "discovery": "curious",
    "reunion": "relieved",
    "loss": "grieving",
    "victory": "triumphant",
    "defeat": "defeated",
    "alliance": "hopeful",
}


def _classify_possession(description: str) -> str | None:
    if not description:
        return None
    lower = description.lower()
    # Check loss first since "gave up" overlaps with "up"
    for verb in LOSS_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", lower):
            return "loss"
    for verb in GAIN_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", lower):
            return "gain"
    return None


class EventReplay:
    """Read events in story order and derive projected state.

    The replay is read-only: it produces lists of facts/snapshots that
    callers (the materializer) persist atomically.
    """

    def __init__(self, db: DBClient) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # public

    def replay(
        self, novel_id: str, through_chapter: int
    ) -> tuple[list[StateSnapshot], list[LocationFact], list[PossessionFact]]:
        chapters = self._load_chapters(novel_id, through_chapter)
        if not chapters:
            return [], [], []

        chapter_id_to_number: dict[str, int] = {
            str(row["id"]): row["number"] for row in chapters
        }

        events = self._load_events(novel_id, through_chapter)
        character_entities = self._character_id_by_entity(novel_id)
        existing_states = self._existing_character_states(novel_id, through_chapter)

        # Track current state per character.
        # state_by_char[character_id][chapter_number] = StateSnapshot
        state_by_char: dict[str, dict[int, StateSnapshot]] = {}

        # Active location edges keyed by entity_id.
        active_location: dict[str, LocationFact] = {}
        location_facts: list[LocationFact] = []

        # Active possession edges keyed by (character_id, object_id).
        active_possession: dict[tuple[str, str], PossessionFact] = {}
        possession_facts: list[PossessionFact] = []

        # Seed snapshots from existing extraction rows (these are richer).
        for row in existing_states:
            character_id = str(row["character_id"])
            chapter_id = str(row["chapter_id"])
            chapter_number = chapter_id_to_number.get(chapter_id)
            if chapter_number is None:
                continue
            snap = StateSnapshot(
                character_id=character_id,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                location_id=str(row["location_id"]) if row["location_id"] else None,
                emotional_state=row["emotional_state"],
                goals=row["goals"],
                knowledge=list(row["knowledge"] or []),
                physical_state=row["physical_state"],
                appearance=row["appearance"],
                notes=row["notes"],
            )
            state_by_char.setdefault(character_id, {})[chapter_number] = snap

        # Walk events in chronological order.
        for event in events:
            chapter_id = str(event["chapter_id"])
            chapter_number = chapter_id_to_number.get(chapter_id)
            if chapter_number is None:
                continue

            involved_characters = [str(c) for c in (event.get("involved_characters") or [])]
            involved_locations = [str(loc) for loc in (event.get("involved_locations") or [])]
            involved_objects = [str(obj) for obj in (event.get("involved_objects") or [])]
            event_type = (event.get("event_type") or "").lower()
            event_id = str(event["id"])
            description = event.get("description") or ""

            # --- location inference ---
            primary_location = involved_locations[0] if involved_locations else None
            for character_id in involved_characters:
                # Update / create snapshot for this character + chapter.
                self._ensure_snapshot(
                    state_by_char,
                    character_id,
                    chapter_id,
                    chapter_number,
                )
                snap = state_by_char[character_id][chapter_number]

                if primary_location is not None:
                    # Fill empty fields from derived signal (do not overwrite).
                    if snap.location_id is None:
                        state_by_char[character_id][chapter_number] = replace(
                            snap, location_id=primary_location
                        )

                # Emotional hint by event_type.
                if event_type in EMOTIONAL_HINTS:
                    snap = state_by_char[character_id][chapter_number]
                    if snap.emotional_state is None:
                        state_by_char[character_id][chapter_number] = replace(
                            snap, emotional_state=EMOTIONAL_HINTS[event_type]
                        )

                # Knowledge gain on revelations.
                if event_type == "revelation" and description:
                    snap = state_by_char[character_id][chapter_number]
                    if description not in snap.knowledge:
                        new_knowledge = list(snap.knowledge) + [description]
                        state_by_char[character_id][chapter_number] = replace(
                            snap, knowledge=new_knowledge
                        )

            # --- location edges ---
            if primary_location is not None:
                # For each involved character that maps to an entity_id, emit a location fact.
                for character_id in involved_characters:
                    entity_id = character_entities.get(character_id)
                    if entity_id is None:
                        continue
                    current = active_location.get(entity_id)
                    if current is None or current.location_id != primary_location:
                        new_fact = LocationFact(
                            entity_id=entity_id,
                            location_id=primary_location,
                            since_chapter=chapter_number,
                            until_chapter=None,
                            evidence_event_id=event_id,
                            certainty=0.8,
                        )
                        active_location[entity_id] = new_fact
                        location_facts.append(new_fact)

            # --- possession edges ---
            possession_kind = _classify_possession(description)
            if possession_kind and involved_objects and involved_characters:
                # Default: the first involved character is the actor.
                actor = involved_characters[0]
                for object_id in involved_objects:
                    key = (actor, object_id)
                    if possession_kind == "gain":
                        if key not in active_possession:
                            fact = PossessionFact(
                                character_id=actor,
                                object_id=object_id,
                                since_chapter=chapter_number,
                                until_chapter=None,
                                evidence_event_id=event_id,
                                certainty=0.7,
                            )
                            active_possession[key] = fact
                            possession_facts.append(fact)
                    elif possession_kind == "loss":
                        # Loss only takes effect if currently held.
                        existing = active_possession.pop(key, None)
                        if existing is not None:
                            # Replace the existing open fact with one that closes
                            # at this chapter. We rewrite the list entry too.
                            closed = replace(
                                existing,
                                until_chapter=chapter_number,
                                evidence_event_id=event_id,
                            )
                            # find and replace last occurrence
                            for i in range(len(possession_facts) - 1, -1, -1):
                                f = possession_facts[i]
                                if (
                                    f.character_id == existing.character_id
                                    and f.object_id == existing.object_id
                                    and f.since_chapter == existing.since_chapter
                                    and f.until_chapter is None
                                ):
                                    possession_facts[i] = closed
                                    break

        # Flatten snapshots.
        snapshots: list[StateSnapshot] = []
        for by_chapter in state_by_char.values():
            for snap in by_chapter.values():
                snapshots.append(snap)

        return snapshots, location_facts, possession_facts

    # ------------------------------------------------------------------
    # helpers

    def _ensure_snapshot(
        self,
        state_by_char: dict[str, dict[int, StateSnapshot]],
        character_id: str,
        chapter_id: str,
        chapter_number: int,
    ) -> None:
        if character_id not in state_by_char:
            state_by_char[character_id] = {}
        if chapter_number not in state_by_char[character_id]:
            # Carry forward last snapshot if any.
            prior = None
            for prev_chapter in sorted(state_by_char[character_id].keys()):
                if prev_chapter < chapter_number:
                    prior = state_by_char[character_id][prev_chapter]
            if prior is not None:
                state_by_char[character_id][chapter_number] = StateSnapshot(
                    character_id=character_id,
                    chapter_id=chapter_id,
                    chapter_number=chapter_number,
                    location_id=prior.location_id,
                    emotional_state=None,  # emotional state is event-driven; do not carry
                    goals=prior.goals,
                    knowledge=list(prior.knowledge),
                    physical_state=prior.physical_state,
                    appearance=prior.appearance,
                    notes=None,
                )
            else:
                state_by_char[character_id][chapter_number] = StateSnapshot(
                    character_id=character_id,
                    chapter_id=chapter_id,
                    chapter_number=chapter_number,
                )

    def _load_chapters(self, novel_id: str, through_chapter: int) -> list[dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT id, number
              FROM chapters
             WHERE novel_id = %s AND number <= %s
             ORDER BY number ASC
            """,
            (novel_id, through_chapter),
            dict_rows=True,
        )

    def _load_events(self, novel_id: str, through_chapter: int) -> list[dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT e.id,
                   e.chapter_id,
                   e.description,
                   e.event_type,
                   e.involved_characters,
                   e.involved_locations,
                   e.involved_objects,
                   e.created_at,
                   c.number AS chapter_number
              FROM events e
              JOIN chapters c ON c.id = e.chapter_id
             WHERE c.novel_id = %s AND c.number <= %s
             ORDER BY c.number ASC,
                      e.created_at ASC,
                      e.id ASC
            """,
            (novel_id, through_chapter),
            dict_rows=True,
        )

    def _character_id_by_entity(self, novel_id: str) -> dict[str, str]:
        """Map character.id -> entity.id for use in located_in_edges."""
        rows = self.db.fetchall(
            """
            SELECT id, entity_id
              FROM characters
             WHERE novel_id = %s AND entity_id IS NOT NULL
            """,
            (novel_id,),
            dict_rows=True,
        )
        return {str(row["id"]): str(row["entity_id"]) for row in rows}

    def _existing_character_states(
        self, novel_id: str, through_chapter: int
    ) -> list[dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT cs.character_id,
                   cs.chapter_id,
                   cs.location_id,
                   cs.emotional_state,
                   cs.goals,
                   cs.knowledge,
                   cs.physical_state,
                   cs.appearance,
                   cs.notes,
                   c.number AS chapter_number
              FROM character_states cs
              JOIN chapters c ON c.id = cs.chapter_id
             WHERE c.novel_id = %s AND c.number <= %s
            """,
            (novel_id, through_chapter),
            dict_rows=True,
        )


__all__ = ["EventReplay"]
