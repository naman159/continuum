"""Fold typed state_deltas (chapter order, insertion order) into projections.

Read-only: produces snapshots/facts; the materializer persists them.
subject_id/object_id in state_deltas are universal entity ids; this module
maps them to typed character/object ids where the projection tables need
those. Deltas naming entities that no longer resolve are skipped.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from pipeline.db.client import DBClient
from pipeline.state.types import LocationFact, PossessionFact, StateSnapshot

logger = logging.getLogger(__name__)

_STATUS_FIELDS = {"emotional_state", "goals", "physical_state", "appearance", "notes"}


class _CursorReader:
    """Runs replay's reads on one caller-supplied cursor.

    DBClient.fetchall checks out a fresh pooled connection per call, so
    replay's reads would otherwise land in four different transaction
    snapshots. A chapter committed between them yields deltas whose chapter_id
    is missing from the map built by the first read, and those deltas are
    dropped silently.
    """

    def __init__(self, cur: Any) -> None:
        self._cur = cur

    def fetchall(self, query: str, params=None, *, dict_rows: bool = False, **_):
        self._cur.execute(query, params)
        rows = self._cur.fetchall()
        if not dict_rows:
            return list(rows)
        cols = [d[0] for d in self._cur.description]
        return [dict(zip(cols, row)) for row in rows]


class StateReplay:
    def __init__(self, db: DBClient) -> None:
        self.db = db

    def replay(
        self, novel_id: str, through_chapter: int, cur: Any = None
    ) -> tuple[list[StateSnapshot], list[LocationFact], list[PossessionFact]]:
        # When a cursor is supplied, every read runs inside the caller's
        # transaction — one consistent snapshot for the whole replay.
        db = _CursorReader(cur) if cur is not None else self.db
        chapters = db.fetchall(
            "SELECT id, number FROM chapters WHERE novel_id = %s AND number <= %s ORDER BY number",
            (novel_id, through_chapter), dict_rows=True,
        )
        if not chapters:
            return [], [], []
        chapter_number_by_id = {str(r["id"]): r["number"] for r in chapters}
        chapter_id_by_number = {r["number"]: str(r["id"]) for r in chapters}

        char_by_entity = {
            str(r["entity_id"]): str(r["id"])
            for r in db.fetchall(
                "SELECT id, entity_id FROM characters WHERE novel_id = %s AND entity_id IS NOT NULL",
                (novel_id,), dict_rows=True,
            )
        }
        object_by_entity = {
            str(r["entity_id"]): str(r["id"])
            for r in db.fetchall(
                "SELECT id, entity_id FROM objects WHERE novel_id = %s AND entity_id IS NOT NULL",
                (novel_id,), dict_rows=True,
            )
        }

        deltas = db.fetchall(
            """
            SELECT d.kind, d.subject_id, d.object_id, d.location_id, d.change,
                   d.attribute, d.detail, d.certainty, d.event_id, d.chapter_id, d.ordinal
              FROM state_deltas d
              JOIN chapters c ON c.id = d.chapter_id
             WHERE c.novel_id = %s AND c.number <= %s
             ORDER BY c.number ASC, d.ordinal ASC, d.id ASC
            """,
            (novel_id, through_chapter), dict_rows=True,
        )

        # Knowledge has one owner: enriched assertions in knows_edges. Merge
        # those events into chapter order so knowledge-only chapters still carry
        # location/goals/condition forward, exactly like status changes.
        learnings = db.fetchall(
            """
            SELECT c.entity_id AS subject_id, k.fact_description AS detail,
                   ch.id AS chapter_id, 'knowledge' AS kind
              FROM knows_edges k
              JOIN characters c ON c.id = k.character_id
              JOIN chapters ch ON ch.novel_id = c.novel_id AND ch.number = k.learned_chapter
             WHERE c.novel_id = %s AND k.learned_chapter <= %s
               AND k.superseded_by_id IS NULL
             ORDER BY k.learned_chapter, k.created_at, k.id
            """,
            (novel_id, through_chapter), dict_rows=True,
        )
        events = sorted(
            [*deltas, *learnings],
            key=lambda event: (chapter_number_by_id[str(event["chapter_id"])], event.get("ordinal", 0)),
        )

        # snapshots[character_id][chapter_number] = StateSnapshot
        snapshots: dict[str, dict[int, StateSnapshot]] = {}
        active_location: dict[str, LocationFact] = {}
        location_facts: list[LocationFact] = []
        active_possession: dict[tuple[str, str], PossessionFact] = {}
        possession_facts: list[PossessionFact] = []

        def snapshot_for(character_id: str, chapter_number: int) -> StateSnapshot:
            per_char = snapshots.setdefault(character_id, {})
            if chapter_number not in per_char:
                prior = None
                for n in sorted(per_char):
                    if n < chapter_number:
                        prior = per_char[n]
                per_char[chapter_number] = StateSnapshot(
                    character_id=character_id,
                    chapter_id=chapter_id_by_number[chapter_number],
                    chapter_number=chapter_number,
                    location_id=prior.location_id if prior else None,
                    goals=prior.goals if prior else None,
                    knowledge=list(prior.knowledge) if prior else [],
                    physical_state=prior.physical_state if prior else None,
                    appearance=prior.appearance if prior else None,
                )
            return per_char[chapter_number]

        for d in events:
            chapter_number = chapter_number_by_id.get(str(d["chapter_id"]))
            if chapter_number is None:
                continue
            subject_entity = str(d["subject_id"])
            character_id = char_by_entity.get(subject_entity)
            kind = d["kind"]

            if kind == "location" and d["location_id"] is not None:
                location_id = str(d["location_id"])
                current = active_location.get(subject_entity)
                if current is None or current.location_id != location_id:
                    fact = LocationFact(
                        entity_id=subject_entity,
                        location_id=location_id,
                        since_chapter=chapter_number,
                        evidence_event_id=str(d["event_id"]) if d["event_id"] else None,
                        certainty=float(d["certainty"] or 1.0),
                    )
                    active_location[subject_entity] = fact
                    location_facts.append(fact)
                if character_id is not None:
                    snap = snapshot_for(character_id, chapter_number)
                    snapshots[character_id][chapter_number] = replace(
                        snap, location_id=location_id
                    )

            elif kind == "possession" and character_id is not None and d["object_id"]:
                object_id = object_by_entity.get(str(d["object_id"]))
                if object_id is None:
                    continue
                key = (character_id, object_id)
                if d["change"] == "gain":
                    if key not in active_possession:
                        fact = PossessionFact(
                            character_id=character_id,
                            object_id=object_id,
                            since_chapter=chapter_number,
                            evidence_event_id=str(d["event_id"]) if d["event_id"] else None,
                            certainty=float(d["certainty"] or 1.0),
                        )
                        active_possession[key] = fact
                        possession_facts.append(fact)
                elif d["change"] == "loss":
                    existing = active_possession.pop(key, None)
                    if existing is not None:
                        closed = replace(existing, until_chapter=chapter_number)
                        for i in range(len(possession_facts) - 1, -1, -1):
                            f = possession_facts[i]
                            if (f.character_id, f.object_id, f.since_chapter, f.until_chapter) == (
                                existing.character_id, existing.object_id,
                                existing.since_chapter, None,
                            ):
                                possession_facts[i] = closed
                                break

            elif kind == "knowledge" and character_id is not None and d["detail"]:
                snap = snapshot_for(character_id, chapter_number)
                if d["detail"] not in snap.knowledge:
                    snapshots[character_id][chapter_number] = replace(
                        snap, knowledge=list(snap.knowledge) + [d["detail"]]
                    )

            elif kind == "status" and character_id is not None:
                attribute, value = d["attribute"], d["detail"]
                if attribute in _STATUS_FIELDS and value:
                    snap = snapshot_for(character_id, chapter_number)
                    snapshots[character_id][chapter_number] = replace(
                        snap, **{attribute: value}
                    )

        flat = [s for per_char in snapshots.values() for s in per_char.values()]
        return flat, location_facts, possession_facts


__all__ = ["StateReplay"]
