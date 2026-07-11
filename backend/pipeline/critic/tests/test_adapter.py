"""build_draft_from_extraction maps extraction output to critic claims
without LLM calls; unknown names drop claims read-only."""

from __future__ import annotations

import uuid

import pytest

from pipeline.critic.adapter import build_draft_from_extraction
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
    assert len(draft.possession_claims) == 1      # unknown character dropped
    assert len(draft.location_claims) == 1
    assert len(draft.knowledge_claims) == 1
    assert draft.knowledge_claims[0]["learned_this_chapter"] is True
    assert len(draft.mentions) == 1
    assert draft.mentions[0]["predicate"] == "eye_color"
    assert len(draft.events) == 1
    assert draft.planned_thread_ids == [] and draft.planned_commitment_ids == []
