"""build_draft_chapter maps LLM draft claims to critic claims, resolving names
read-only: an unknown name drops the claim rather than minting an entity."""

from __future__ import annotations

import uuid

import pytest

from pipeline.critic.adapter import build_draft_chapter
from pipeline.critic.runner import ContinuityCritic
from pipeline.critic.types import Severity
from pipeline.db.client import DBClient
from pipeline.extraction.resolver import EntityResolver


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


def test_mcp_claims_collapse_to_one_location_per_character(db: DBClient, seeded):
    """build_draft_chapter used to emit one location claim per LLM assertion.

    check_location_possession FAILs a character with >1 distinct location in a
    chapter, so a character who simply walked from one place to another failed
    the critique — and under "block" that refuses the write.
    The claim that matters is where they end up.
    """
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
        "a character moving within one chapter must not FAIL the critique"
    )


def test_location_claims_collapse_on_resolved_id_not_surface_name(db: DBClient, seeded):
    """Two surface forms of one character must collapse to a single claim.

    Keying the collapse on the raw name let an alias the resolver already
    knows produce two claims that both resolved to the same character_id,
    which the check then read as 'asserted in 2 locations'.
    """
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
