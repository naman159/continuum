from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from pipeline.generation.draft_claims import build_draft_chapter, extract_draft_claims

CHAR_ID = str(uuid.uuid4())
CHAR_ENTITY_ID = str(uuid.uuid4())
LOC_ID = str(uuid.uuid4())
OBJ_ID = str(uuid.uuid4())


class ClaimsFakeDB:
    """Read-only name lookups: knows Jake (character), Harbor (location), Knife (object)."""

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        name = str(params[1]).lower() if params and len(params) > 1 else ""
        if "FROM characters" in query and name == "jake":
            return (CHAR_ID, CHAR_ENTITY_ID)
        if "FROM locations" in query and name == "harbor":
            return (LOC_ID, str(uuid.uuid4()))
        if "FROM objects" in query and name == "knife":
            return (OBJ_ID, str(uuid.uuid4()))
        return None


RAW = {
    "mentions": [
        {"entity_name": "Jake", "entity_type": "character", "predicate": "eye_color",
         "claimed_value": "green", "quote": "his green eyes"},
        {"entity_name": "Nobody", "entity_type": "character", "predicate": "x",
         "claimed_value": "y", "quote": "z"},  # unknown — dropped
    ],
    "knowledge_claims": [
        {"character_name": "Jake", "fact_description": "the ledger is forged",
         "source_type": "inference", "quote": "Jake knew the ledger was forged"},
    ],
    "location_claims": [{"character_name": "Jake", "location_name": "Harbor", "quote": "at the harbor"}],
    "possession_claims": [{"character_name": "Jake", "object_name": "Knife", "quote": "his knife"}],
    "events": [{"description": "Jake confronts Sara", "event_type": "conflict"}],
}


def test_build_draft_chapter_resolves_names_read_only():
    draft = build_draft_chapter(
        ClaimsFakeDB(), novel_id="n1", chapter_number=5, text="prose",
        raw_claims=RAW, planned_thread_ids=["t1"], planned_commitment_ids=["c1"],
    )
    assert draft.mentions == [
        {"entity_id": CHAR_ENTITY_ID, "predicate": "eye_color",
         "claimed_value": "green", "quote": "his green eyes"}
    ]
    assert draft.knowledge_claims[0]["character_id"] == CHAR_ID
    assert draft.location_claims[0] == {"character_id": CHAR_ID, "location_id": LOC_ID, "quote": "at the harbor"}
    assert draft.possession_claims[0]["object_id"] == OBJ_ID
    assert draft.events[0]["description"] == "Jake confronts Sara"
    assert draft.planned_thread_ids == ["t1"]


def test_extract_draft_claims_parses_llm_json():
    def fake_completion(**kwargs):
        msg = SimpleNamespace(content=json.dumps(RAW))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    out = extract_draft_claims("prose", use_mock=False, completion_fn=fake_completion)
    assert out["mentions"][0]["entity_name"] == "Jake"


def test_extract_draft_claims_mock_is_empty():
    out = extract_draft_claims("prose", use_mock=True)
    assert out == {"mentions": [], "knowledge_claims": [], "location_claims": [],
                   "possession_claims": [], "events": []}


def test_extract_draft_claims_bad_json_safe():
    def fake_completion(**kwargs):
        msg = SimpleNamespace(content="not json")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    out = extract_draft_claims("prose", use_mock=False, completion_fn=fake_completion)
    assert out == {k: [] for k in ("mentions", "knowledge_claims", "location_claims",
                                   "possession_claims", "events")}
