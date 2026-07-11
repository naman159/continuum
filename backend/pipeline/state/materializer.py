from __future__ import annotations

import logging
from typing import Any

from pipeline.db.client import DBClient
from pipeline.state.replay import StateReplay
from pipeline.state.types import (
    LocationFact,
    MaterializeResult,
    PossessionFact,
    StateSnapshot,
)

logger = logging.getLogger(__name__)


class StateMaterializer:
    """Persists derived projections (character_states, located_in_edges,
    possesses_edges) from the event log in a single transaction.

    The materializer is idempotent: re-running for the same
    (novel_id, through_chapter) will not produce duplicate rows.
    """

    def __init__(self, db: DBClient) -> None:
        self.db = db
        self.replay = StateReplay(db)

    def materialize(self, novel_id: str, through_chapter: int) -> MaterializeResult:
        snapshots, location_facts, possession_facts = self.replay.replay(
            novel_id, through_chapter
        )

        snapshots_written = 0
        location_edges_written = 0
        possession_edges_written = 0

        with self.db.transaction() as cur:
            snapshots_written = self._write_character_states(cur, novel_id, snapshots)
            location_edges_written = self._write_location_edges(
                cur, novel_id, location_facts
            )
            possession_edges_written = self._write_possession_edges(
                cur, novel_id, possession_facts
            )
            cur.execute(
                """
                INSERT INTO materialized_state_runs (novel_id, through_chapter, notes)
                VALUES (%s, %s, %s)
                """,
                (
                    novel_id,
                    through_chapter,
                    (
                        f"snapshots={snapshots_written} "
                        f"location_edges={location_edges_written} "
                        f"possession_edges={possession_edges_written}"
                    ),
                ),
            )

        return MaterializeResult(
            novel_id=novel_id,
            through_chapter=through_chapter,
            snapshots_written=snapshots_written,
            location_edges_written=location_edges_written,
            possession_edges_written=possession_edges_written,
        )

    # ------------------------------------------------------------------
    # character_states: sole writer — novel-scoped DELETE-then-INSERT.

    def _write_character_states(
        self, cur: Any, novel_id: str, snapshots: list[StateSnapshot]
    ) -> int:
        # Sole writer: rebuild the whole novel's snapshot projection so
        # snapshots whose source deltas disappeared don't survive.
        cur.execute(
            """
            DELETE FROM character_states
             WHERE character_id IN (SELECT id FROM characters WHERE novel_id = %s)
            """,
            (novel_id,),
        )
        if not snapshots:
            return 0

        for snap in snapshots:
            cur.execute(
                """
                INSERT INTO character_states (
                    character_id, chapter_id, location_id, emotional_state,
                    goals, knowledge, physical_state, appearance, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    snap.character_id,
                    snap.chapter_id,
                    snap.location_id,
                    snap.emotional_state,
                    snap.goals,
                    list(snap.knowledge or []),
                    snap.physical_state,
                    snap.appearance,
                    snap.notes,
                ),
            )

        return len(snapshots)

    # ------------------------------------------------------------------
    # located_in_edges: invalidate prior open edges for the same entity
    # when a different location appears, then insert. Idempotent.

    def _write_location_edges(
        self, cur: Any, novel_id: str, facts: list[LocationFact]
    ) -> int:
        # Rebuild the whole novel's projection: delete before the empty-facts
        # return, and scope by novel rather than by the entities present in
        # the new facts — otherwise edges whose source events disappeared
        # (e.g. a chapter re-processed with replace=True) survive as stale
        # rows. Safe because the events table is the ground truth and we are
        # re-deriving from scratch.
        cur.execute(
            """
            DELETE FROM located_in_edges
             WHERE entity_id IN (SELECT id FROM entities WHERE novel_id = %s)
            """,
            (novel_id,),
        )

        if not facts:
            return 0

        # Group facts per entity in arrival order, then chain them so that
        # each prior fact's until_chapter == next.since_chapter - 1 and
        # superseded_by_id == next.id.
        by_entity: dict[str, list[LocationFact]] = {}
        for fact in facts:
            by_entity.setdefault(fact.entity_id, []).append(fact)

        written = 0
        for entity_id, entity_facts in by_entity.items():
            inserted_ids: list[str] = []
            for fact in entity_facts:
                row = cur.execute(
                    """
                    INSERT INTO located_in_edges (
                        entity_id, location_id, since_chapter, until_chapter,
                        evidence_event_id, certainty
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        fact.entity_id,
                        fact.location_id,
                        fact.since_chapter,
                        fact.until_chapter,
                        fact.evidence_event_id,
                        fact.certainty,
                    ),
                )
                new_id = cur.fetchone()[0]
                inserted_ids.append(str(new_id))
                written += 1

            # Now stitch consecutive edges.
            for i in range(len(entity_facts) - 1):
                prior_id = inserted_ids[i]
                next_fact = entity_facts[i + 1]
                next_id = inserted_ids[i + 1]
                cur.execute(
                    """
                    UPDATE located_in_edges
                       SET until_chapter = %s,
                           superseded_by_id = %s::uuid
                     WHERE id = %s::uuid
                    """,
                    (next_fact.since_chapter - 1, next_id, prior_id),
                )

        return written

    # ------------------------------------------------------------------
    # possesses_edges: same invalidate-don't-delete pattern.

    def _write_possession_edges(
        self, cur: Any, novel_id: str, facts: list[PossessionFact]
    ) -> int:
        # Same novel-scoped rebuild as _write_location_edges: stale edges for
        # characters absent from the new facts must not survive.
        cur.execute(
            """
            DELETE FROM possesses_edges
             WHERE character_id IN (SELECT id FROM characters WHERE novel_id = %s)
            """,
            (novel_id,),
        )

        if not facts:
            return 0

        # Insert. The replay layer already closed loss events, so we just
        # persist the facts as-is.
        written = 0
        for fact in facts:
            cur.execute(
                """
                INSERT INTO possesses_edges (
                    character_id, object_id, since_chapter, until_chapter,
                    evidence_event_id, certainty
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    fact.character_id,
                    fact.object_id,
                    fact.since_chapter,
                    fact.until_chapter,
                    fact.evidence_event_id,
                    fact.certainty,
                ),
            )
            written += 1

        return written


def materialize_state(
    novel_id: str, through_chapter: int, db: DBClient | None = None
) -> MaterializeResult:
    """Convenience wrapper that opens a DBClient if one is not supplied."""
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        return StateMaterializer(client).materialize(novel_id, through_chapter)
    finally:
        if owned:
            client.close()


__all__ = ["StateMaterializer", "materialize_state"]
