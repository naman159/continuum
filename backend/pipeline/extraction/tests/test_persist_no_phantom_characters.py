from __future__ import annotations

"""Regression: reference-only passes must never mint a character row.

Game-system nouns (skills, classes, tutorials) and other non-characters used to
appear in the characters table because any downstream pass that *named* a
character auto-created one via resolve_character. The new_entities pass is now
the sole authoritative creator; everything else resolves-or-skips.
"""

import uuid

from pipeline.pipeline import _persist_extraction


class RecordingDB:
    """Fake DB that answers resolver lookups (nothing exists) and records every
    INSERT so a test can assert no `characters` row was created."""

    def __init__(self) -> None:
        self.inserted_tables: list[str] = []

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        return None  # nothing pre-exists

    def fetchval(self, query, params=None, *, commit=False):
        q = query.strip().lower()
        if "insert into characters" in q:
            self.inserted_tables.append("characters")
        elif "insert into events" in q:
            self.inserted_tables.append("events")
        elif "insert into entities" in q:
            self.inserted_tables.append("entities")
        return uuid.uuid4()

    def execute(self, query, params=None):
        pass


def _base_extraction() -> dict:
    return {
        "new_entities": {},
        "custom_entities": [],
        "entity_deltas": [],
        "events": [],
        "thread_updates": [],
        "continuity_flags": [],
        "relationship_updates": [],
        "dynamics_updates": [],
        "canon_facts": [],
    }


def _run(extracted: dict) -> RecordingDB:
    from pipeline.extraction.resolver import EntityResolver

    db = RecordingDB()
    resolver = EntityResolver(db, novel_id="n1", chapter_number=1)
    _persist_extraction(
        db, resolver=resolver, chapter_id=str(uuid.uuid4()),
        chapter_number=1, extracted=extracted,
    )
    return db


def test_event_involving_unknown_name_does_not_create_character():
    extracted = _base_extraction()
    extracted["events"] = [
        {
            "description": "The tutorial window appears.",
            "event_type": "other",
            "impact_level": "low",
            "involved_characters": ["Tutorial", "Skills", "Archer Class"],
            "involved_locations": [],
            "involved_objects": [],
            "involved_factions": [],
        }
    ]
    db = _run(extracted)
    assert "characters" not in db.inserted_tables
    # The event itself is still recorded (just with no involved characters).
    assert "events" in db.inserted_tables


def test_entity_delta_for_unknown_name_does_not_create_character():
    extracted = _base_extraction()
    extracted["entity_deltas"] = [
        {"character_name": "Healer Class", "emotional_state": "n/a", "knowledge": []}
    ]
    db = _run(extracted)
    assert "characters" not in db.inserted_tables


def test_new_entities_character_is_still_created():
    """The authoritative pass must keep creating characters."""
    extracted = _base_extraction()
    extracted["new_entities"] = {
        "characters": [{"name": "Jake", "aliases": [], "description": "protagonist"}]
    }
    db = _run(extracted)
    assert "characters" in db.inserted_tables
