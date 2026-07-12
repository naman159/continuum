"""Shared seed factory for reads-layer tests against real branch-isolated Postgres.

Seeds one small, internally-consistent 3-chapter novel touching every table
the read layer (and later the api/mcp callers) query: characters, locations,
an object, a faction, a plot thread with thread events, a commitment, typed
state_deltas replayed through the materializer, a canon fact, a critique
report with a finding, scenes, a relationship, and a shared dynamic.

Usage (see reads/tests/conftest.py for the fixture wrapper):

    novel_id = None
    try:
        seeded = seed_novel(db)
        novel_id = seeded["novel_id"]
        ...
    finally:
        if novel_id is not None:
            cleanup(db, novel_id)
"""

from __future__ import annotations

import uuid
from typing import Any

from pipeline.db.client import DBClient
from pipeline.state.materializer import StateMaterializer


def seed_novel(db: DBClient) -> dict[str, Any]:
    """Seed a 3-chapter novel and return every id/name later tests need.

    Story shape:
        ch1: char A (Aria Vance) appears; opens the plot thread; foreshadows
             the commitment; located at Fogmere Docks; gains the Iron
             Compass; two scenes.
        ch2: canon fact recorded about char A; the critique report (failed,
             one finding) is filed against this chapter.
        ch3: char B (Borin Thale) first appears; the plot thread closes; the
             commitment pays off; a faction-involving event occurs; char A
             and char B share a relationship and a shared dynamic.
    """
    novel_id = str(uuid.uuid4())
    title = f"ReadsTestNovel-{novel_id[:8]}"

    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO novels (id, title) VALUES (%s, %s)",
            (novel_id, title),
        )

        # ---- 3 chapters ----
        chapter_ids: list[str] = []
        for n in range(1, 4):
            cur.execute(
                """
                INSERT INTO chapters (novel_id, number, raw_text)
                VALUES (%s, %s, %s) RETURNING id
                """,
                (novel_id, n, f"chapter {n} text"),
            )
            chapter_ids.append(str(cur.fetchone()[0]))

        def make_entity(kind: str, name: str) -> str:
            cur.execute(
                "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,%s,%s) RETURNING id",
                (novel_id, kind, name),
            )
            return str(cur.fetchone()[0])

        # ---- two characters: A first-seen ch1, B first-seen ch3 ----
        char_a_name = "Aria Vance"
        char_b_name = "Borin Thale"
        char_a_eid = make_entity("character", char_a_name)
        char_b_eid = make_entity("character", char_b_name)

        cur.execute(
            """
            INSERT INTO characters (novel_id, entity_id, name, first_appearance_chapter)
            VALUES (%s,%s,%s,%s) RETURNING id
            """,
            (novel_id, char_a_eid, char_a_name, 1),
        )
        char_a_id = str(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO characters (novel_id, entity_id, name, first_appearance_chapter)
            VALUES (%s,%s,%s,%s) RETURNING id
            """,
            (novel_id, char_b_eid, char_b_name, 3),
        )
        char_b_id = str(cur.fetchone()[0])

        # ---- two locations, one object ----
        loc_a_name = "Fogmere Docks"
        loc_b_name = "Sable Archive"
        loc_a_eid = make_entity("location", loc_a_name)
        loc_b_eid = make_entity("location", loc_b_name)
        cur.execute(
            "INSERT INTO locations (novel_id, entity_id, name, first_appearance_chapter) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (novel_id, loc_a_eid, loc_a_name, 1),
        )
        loc_a_id = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO locations (novel_id, entity_id, name, first_appearance_chapter) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (novel_id, loc_b_eid, loc_b_name, 3),
        )
        loc_b_id = str(cur.fetchone()[0])

        obj_name = "Iron Compass"
        obj_eid = make_entity("object", obj_name)
        cur.execute(
            "INSERT INTO objects (novel_id, entity_id, name, first_appearance_chapter) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (novel_id, obj_eid, obj_name, 1),
        )
        obj_id = str(cur.fetchone()[0])

        # ---- faction + a faction-involving event in ch3 ----
        faction_name = "The Hollow Ledger"
        faction_eid = make_entity("faction", faction_name)
        cur.execute(
            "INSERT INTO factions (novel_id, entity_id, name) VALUES (%s,%s,%s) RETURNING id",
            (novel_id, faction_eid, faction_name),
        )
        faction_id = str(cur.fetchone()[0])

        # events.involved_* store TYPED ids (characters.id / factions.id / ...):
        # pipeline.py inserts resolver.resolve_*(name).entity_id, which is the
        # type-specific id per ResolvedEntity (resolver.py), not entities.id.
        cur.execute(
            """
            INSERT INTO events (chapter_id, description, event_type, impact_level,
                                 involved_characters, involved_factions)
            VALUES (%s, %s, %s, %s, %s::uuid[], %s::uuid[])
            RETURNING id
            """,
            (
                chapter_ids[2],
                "The Hollow Ledger ambushes the caravan at Sable Archive.",
                "conflict",
                "high",
                [char_a_id],
                [faction_id],
            ),
        )
        faction_event_id = str(cur.fetchone()[0])

        # ---- plot thread opened ch1, closed ch3, with thread_events in both ----
        cur.execute(
            """
            INSERT INTO events (chapter_id, description, event_type, impact_level,
                                 involved_characters)
            VALUES (%s, %s, %s, %s, %s::uuid[])
            RETURNING id
            """,
            (chapter_ids[0], "Aria discovers the ledger's trail.", "discovery", "medium",
             [char_a_id]),
        )
        thread_open_event_id = str(cur.fetchone()[0])

        cur.execute(
            """
            INSERT INTO plot_threads (novel_id, title, description, status,
                                       opened_chapter, closed_chapter, thread_type)
            VALUES (%s, %s, %s, 'closed', %s, %s, %s) RETURNING id
            """,
            (novel_id, "The Ledger's Trail", "Aria hunts down the Hollow Ledger.",
             1, 3, "main"),
        )
        thread_id = str(cur.fetchone()[0])

        cur.execute(
            "INSERT INTO thread_events (thread_id, event_id, impact) VALUES (%s,%s,%s)",
            (thread_id, thread_open_event_id, "opens"),
        )
        cur.execute(
            "INSERT INTO thread_events (thread_id, event_id, impact) VALUES (%s,%s,%s)",
            (thread_id, faction_event_id, "closes"),
        )

        # ---- commitment: foreshadowed ch1, paid off ch3, satisfied ----
        cur.execute(
            """
            INSERT INTO commitments (novel_id, foreshadow_text, foreshadow_chapter,
                                      payoff_text, payoff_chapter, status)
            VALUES (%s, %s, %s, %s, %s, 'satisfied') RETURNING id
            """,
            (
                novel_id,
                "Aria notices a ledger stamped with a hollow sigil.",
                1,
                "The Hollow Ledger's ambush confirms the sigil was theirs all along.",
                3,
            ),
        )
        commitment_id = str(cur.fetchone()[0])

        # ---- typed state_deltas (location + possession) + materialize ----
        cur.execute(
            """
            INSERT INTO state_deltas (chapter_id, ordinal, kind, subject_id,
                                      location_id, change)
            VALUES (%s, %s, 'location', %s, %s, 'move')
            """,
            (chapter_ids[0], 0, char_a_eid, loc_a_id),
        )
        cur.execute(
            """
            INSERT INTO state_deltas (chapter_id, ordinal, kind, subject_id,
                                      object_id, change)
            VALUES (%s, %s, 'possession', %s, %s, 'gain')
            """,
            (chapter_ids[0], 1, char_a_eid, obj_eid),
        )

        # ---- one canon fact, source_chapter=2 ----
        cur.execute(
            """
            INSERT INTO canon_facts (novel_id, kind, subject_entity_id, predicate,
                                      value, source_chapter)
            VALUES (%s, 'trait', %s, 'eye_color', 'storm-grey', %s) RETURNING id
            """,
            (novel_id, char_a_eid, 2),
        )
        canon_fact_id = str(cur.fetchone()[0])

        # ---- critique report on ch2: failed, one fail-severity finding ----
        cur.execute(
            """
            INSERT INTO critique_reports (chapter_id, passed, stats)
            VALUES (%s, false, %s::jsonb) RETURNING id
            """,
            (chapter_ids[1], "{}"),
        )
        critique_report_id = str(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO critique_findings (report_id, check_name, severity, message)
            VALUES (%s, %s, 'fail', %s) RETURNING id
            """,
            (critique_report_id, "continuity", "Aria's eye color contradicts chapter 1."),
        )
        critique_finding_id = str(cur.fetchone()[0])

        # ---- scenes on ch1 ----
        cur.execute(
            """
            INSERT INTO scenes (chapter_id, scene_index, pov_character_id, location_id, summary)
            VALUES (%s, 0, %s, %s, %s) RETURNING id
            """,
            (chapter_ids[0], char_a_id, loc_a_id, "Aria walks the docks at dawn."),
        )
        scene_1_id = str(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO scenes (chapter_id, scene_index, pov_character_id, location_id, summary)
            VALUES (%s, 1, %s, %s, %s) RETURNING id
            """,
            (chapter_ids[0], char_a_id, loc_a_id, "Aria finds the iron compass in the surf."),
        )
        scene_2_id = str(cur.fetchone()[0])

        # ---- relationship + shared dynamic between the two characters ----
        cur.execute(
            """
            INSERT INTO relationships (entity_a_id, entity_b_id, rel_type, from_chapter,
                                        chapter_id)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (char_a_eid, char_b_eid, "rival", 3, chapter_ids[2]),
        )
        relationship_id = str(cur.fetchone()[0])

        cur.execute(
            """
            INSERT INTO shared_dynamics (entity_a_id, entity_b_id, chapter_id, description)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (char_a_eid, char_b_eid, chapter_ids[2], "Wary respect after the ambush."),
        )
        shared_dynamic_id = str(cur.fetchone()[0])

    # Materialize outside the seeding transaction (StateMaterializer opens its
    # own transaction on the same DBClient).
    StateMaterializer(db).materialize(novel_id, 3)

    return {
        "novel_id": novel_id,
        "chapter_ids": chapter_ids,
        "char_a_id": char_a_id,
        "char_a_name": char_a_name,
        "char_a_eid": char_a_eid,
        "char_b_id": char_b_id,
        "char_b_name": char_b_name,
        "char_b_eid": char_b_eid,
        "loc_a_id": loc_a_id,
        "loc_a_name": loc_a_name,
        "loc_a_eid": loc_a_eid,
        "loc_b_id": loc_b_id,
        "loc_b_name": loc_b_name,
        "loc_b_eid": loc_b_eid,
        "obj_id": obj_id,
        "obj_name": obj_name,
        "obj_eid": obj_eid,
        "faction_id": faction_id,
        "faction_name": faction_name,
        "faction_eid": faction_eid,
        "faction_event_id": faction_event_id,
        "thread_id": thread_id,
        "thread_open_event_id": thread_open_event_id,
        "commitment_id": commitment_id,
        "canon_fact_id": canon_fact_id,
        "critique_report_id": critique_report_id,
        "critique_finding_id": critique_finding_id,
        "scene_ids": [scene_1_id, scene_2_id],
        "relationship_id": relationship_id,
        "shared_dynamic_id": shared_dynamic_id,
    }


def cleanup(db: DBClient, novel_id: str) -> None:
    """Delete a seeded novel; every row above cascades from novels.id."""
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))
