"""build_draft_from_extraction maps extraction output to critic claims
without LLM calls; unknown names drop claims read-only."""

from __future__ import annotations

import uuid

import pytest

from pipeline.critic.adapter import build_draft_from_extraction
from pipeline.critic.runner import ContinuityCritic
from pipeline.critic.types import Severity
from pipeline.db.client import DBClient
from pipeline.extraction.resolver import EntityResolver


@pytest.fixture
def db():
    client = DBClient()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def seeded(db: DBClient):
    novel_id = str(uuid.uuid4())
    db.execute("INSERT INTO novels (id, title) VALUES (%s, %s)", (novel_id, f"T-{novel_id[:8]}"))
    resolver = EntityResolver(db, novel_id=novel_id, chapter_number=1)
    resolver.resolve_character("Aelric", {})
    resolver.resolve_object("silver dagger", {})
    resolver.resolve_location("Pellis Harbor", {})
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_maps_extraction_to_claims(db: DBClient, seeded):
    extracted = {
        "state_deltas": [
            {"kind": "possession", "character_name": "Aelric",
             "object_name": "silver dagger", "change": "gain", "quote": "took it"},
            {"kind": "location", "character_name": "Aelric",
             "location_name": "Pellis Harbor", "change": "move", "quote": "arrived"},
            {"kind": "possession", "character_name": "Nobody",
             "object_name": "silver dagger", "change": "gain", "quote": "q"},
        ],
        "learnings": [
            {"character_name": "Aelric", "fact_description": "the harbor is watched",
             "source_type": "observation"},
        ],
        "canon_facts": [
            {"subject_name": "Aelric", "subject_type": "character",
             "predicate": "eye_color", "value": "grey", "quote": "grey eyes"},
        ],
        "events": [{"description": "Aelric arrives", "event_type": "arrival"}],
    }
    draft = build_draft_from_extraction(
        db, novel_id=seeded, chapter_number=2, text="prose", extracted=extracted
    )
    # possession gains are never mapped to claims (self-evidencing pickups —
    # see build_draft_from_extraction); the pre-save MCP path still gets
    # possession claims from the LLM draft-claims extractor.
    assert draft.possession_claims == []
    assert len(draft.location_claims) == 1
    assert len(draft.knowledge_claims) == 1
    assert draft.knowledge_claims[0]["learned_this_chapter"] is True
    assert len(draft.mentions) == 1
    assert draft.mentions[0]["predicate"] == "eye_color"
    assert len(draft.events) == 1
    assert draft.planned_thread_ids == [] and draft.planned_commitment_ids == []


def test_movement_pickup_and_inference_do_not_false_flag(db: DBClient):
    """Regression test for the spine adapter's three reconciliations:
    - intra-chapter movement (two location deltas) collapses to one claim
      (the last), so location_possession does not FAIL a character for
      legitimately moving during the chapter;
    - a possession gain produces no claim at all, so location_possession does
      not WARN on every fresh pickup;
    - an inference-sourced learning with learned_this_chapter=True is exempt
      from knowledge_state, matching the flag's meaning regardless of
      source_type.
    """
    novel_id = str(uuid.uuid4())
    db.execute("INSERT INTO novels (id, title) VALUES (%s, %s)", (novel_id, f"T-{novel_id[:8]}"))
    resolver = EntityResolver(db, novel_id=novel_id, chapter_number=3)
    resolver.resolve_character("Aelric", {})
    resolver.resolve_location("Pellis Harbor", {})
    old_mill = resolver.resolve_location("The Old Mill", {})
    resolver.resolve_object("silver dagger", {})

    extracted = {
        "state_deltas": [
            {"kind": "location", "character_name": "Aelric",
             "location_name": "Pellis Harbor", "change": "move", "quote": "left the harbor"},
            {"kind": "location", "character_name": "Aelric",
             "location_name": "The Old Mill", "change": "move", "quote": "arrived at the mill"},
            {"kind": "possession", "character_name": "Aelric",
             "object_name": "silver dagger", "change": "gain", "quote": "picked up the dagger"},
        ],
        "learnings": [
            {"character_name": "Aelric", "fact_description": "the mill is abandoned",
             "source_type": "inference", "learned_this_chapter": True},
        ],
        "canon_facts": [],
        "events": [],
    }

    draft = build_draft_from_extraction(
        db, novel_id=novel_id, chapter_number=3, text="prose", extracted=extracted
    )
    assert draft.possession_claims == []
    assert len(draft.location_claims) == 1
    assert draft.location_claims[0]["location_id"] == old_mill.entity_id

    report = ContinuityCritic(db).critique(draft)
    assert report.passed is True
    location_possession_warns = [
        f for f in report.findings
        if f.check == "location_possession" and f.severity == Severity.WARN
    ]
    assert location_possession_warns == []

    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_mcp_claims_collapse_to_one_location_per_character(db: DBClient, seeded):
    """build_draft_chapter used to emit one location claim per LLM assertion.

    check_location_possession FAILs a character with >1 distinct location in a
    chapter, so a character who simply walked from one place to another failed
    the MCP pre-save gate — the one path where a FAIL actually blocks a save.
    The claim that matters is where they end up.
    """
    from pipeline.critic.adapter import build_draft_chapter

    resolver = EntityResolver(db, novel_id=seeded, chapter_number=2)
    resolver.resolve_location("The Old Mill", {})

    draft = build_draft_chapter(
        db,
        novel_id=seeded,
        chapter_number=2,
        text="Aelric left the harbour for the mill.",
        raw_claims={
            "location_claims": [
                {"character_name": "Aelric", "location_name": "Pellis Harbor"},
                {"character_name": "Aelric", "location_name": "The Old Mill"},
            ],
        },
        planned_thread_ids=[],
        planned_commitment_ids=[],
    )

    assert len(draft.location_claims) == 1
    report = ContinuityCritic(db).critique(draft)
    assert not [f for f in report.findings if f.severity is Severity.FAIL], (
        "a character moving within one chapter must not FAIL the gate"
    )


def test_location_claims_collapse_on_resolved_id_not_surface_name(db: DBClient, seeded):
    """Two surface forms of one character must collapse to a single claim.

    Keying the collapse on the raw name let an alias the resolver already
    knows produce two claims that both resolved to the same character_id,
    which the check then read as 'asserted in 2 locations'.
    """
    from pipeline.critic.adapter import build_draft_chapter

    db.execute(
        "UPDATE characters SET aliases = %s WHERE novel_id = %s AND name = %s",
        (["Lord Aelric"], seeded, "Aelric"),
    )
    resolver = EntityResolver(db, novel_id=seeded, chapter_number=2)
    resolver.resolve_location("The Old Mill", {})

    draft = build_draft_chapter(
        db,
        novel_id=seeded,
        chapter_number=2,
        text="...",
        raw_claims={
            "location_claims": [
                {"character_name": "Aelric", "location_name": "Pellis Harbor"},
                {"character_name": "Lord Aelric", "location_name": "The Old Mill"},
            ],
        },
        planned_thread_ids=[],
        planned_commitment_ids=[],
    )

    assert len(draft.location_claims) == 1
