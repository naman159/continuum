from __future__ import annotations

from pipeline.extraction.prompts import PASS_ORDER, PASS_SCHEMAS, PASS_TASK_INSTRUCTIONS


def test_canon_facts_pass_registered():
    assert "canon_facts" in PASS_ORDER
    assert PASS_ORDER[-1] == "canon_facts"  # appended; jobs total_passes adapts via len()


def test_canon_facts_schema_shape():
    schema = PASS_SCHEMAS["canon_facts"]["canon_facts"][0]
    for key in ("subject_name", "subject_type", "predicate", "value", "kind", "confidence", "quote"):
        assert key in schema


def test_canon_facts_instruction_mentions_durable_and_predicate_style():
    text = PASS_TASK_INSTRUCTIONS["canon_facts"].lower()
    assert "durable" in text or "immutable" in text
    assert "snake_case" in text


from pipeline.extraction.extractor import (
    _normalize_extraction,
    empty_extraction,
    merge_extractions,
)


def test_empty_extraction_has_canon_facts():
    assert empty_extraction()["canon_facts"] == []


def test_normalize_keeps_valid_canon_facts_only():
    raw = {
        "canon_facts": [
            {"subject_name": "Jake", "subject_type": "character", "predicate": "eye_color", "value": "green"},
            {"predicate": "no_subject", "value": "x"},   # missing subject — dropped
            "not a dict",
        ]
    }
    out = _normalize_extraction(raw)
    assert len(out["canon_facts"]) == 1
    assert out["canon_facts"][0]["subject_name"] == "Jake"


def test_merge_extractions_dedupes_canon_by_subject_predicate_keeping_confidence():
    e1 = empty_extraction()
    e1["canon_facts"] = [
        {"subject_name": "Jake", "subject_type": "character", "predicate": "eye_color",
         "value": "green", "confidence": 0.6},
    ]
    e2 = empty_extraction()
    e2["canon_facts"] = [
        {"subject_name": "jake", "subject_type": "character", "predicate": "eye_color",
         "value": "emerald green", "confidence": 0.9},
        {"subject_name": "Jake", "subject_type": "character", "predicate": "home_town",
         "value": "Busan", "confidence": 1.0},
    ]
    merged = merge_extractions([e1, e2])
    facts = {(f["subject_name"].lower(), f["predicate"]): f for f in merged["canon_facts"]}
    assert len(facts) == 2
    assert facts[("jake", "eye_color")]["value"] == "emerald green"  # higher confidence wins


from pipeline.extraction.canonicalizer import collect_names_by_type, _apply_rename_map


def test_canon_subjects_collected_and_renamed():
    extracted = {
        "canon_facts": [
            {"subject_name": "Jane", "subject_type": "character", "predicate": "eye_color", "value": "blue"},
            {"subject_name": "Netherfield", "subject_type": "location", "predicate": "region", "value": "north"},
        ],
    }
    by_type = collect_names_by_type(extracted)
    assert "Jane" in by_type["character"]
    assert "Netherfield" in by_type["location"]

    renamed = _apply_rename_map(
        extracted,
        {"character": {"jane": "Jane Bennet"}, "location": {"netherfield": "Netherfield Park"}},
    )
    assert renamed["canon_facts"][0]["subject_name"] == "Jane Bennet"
    assert renamed["canon_facts"][1]["subject_name"] == "Netherfield Park"


def test_canon_subject_with_unknown_type_is_ignored_not_crashed():
    extracted = {
        "canon_facts": [
            {"subject_name": "Ghost", "subject_type": "person", "predicate": "x", "value": "y"},
        ],
    }
    by_type = collect_names_by_type(extracted)
    assert "Ghost" not in by_type["character"]
    assert "person" not in by_type  # never inserted under a bogus key


def test_canon_rename_leaves_unmapped_types_untouched():
    extracted = {
        "canon_facts": [
            {"subject_name": "The Order", "subject_type": "faction", "predicate": "x", "value": "y"},
        ],
    }
    renamed = _apply_rename_map(extracted, {"character": {"jane": "Jane Bennet"}})
    assert renamed["canon_facts"][0]["subject_name"] == "The Order"


import uuid

from pipeline.extraction.persist_canon import persist_canon_facts


class FakeResolver:
    def __init__(self):
        self.uid = str(uuid.uuid4())

    def resolve_character(self, name, metadata=None):
        from pipeline.extraction.resolver import ResolvedEntity
        return ResolvedEntity(entity_id="typed-id", universal_id=self.uid, created=False)

    resolve_location = resolve_object = resolve_faction = resolve_character


class CanonFakeDB:
    """Scripts fetchone for the existing-fact lookup; records writes."""

    def __init__(self, existing=None):
        self.existing = existing  # dict row or None
        self.calls: list[tuple[str, tuple]] = []

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        self.calls.append((query, tuple(params or ())))
        if "FROM canon_facts" in query:
            return self.existing
        return None

    def execute(self, query, params=None):
        self.calls.append((query, tuple(params or ())))


FACT = {
    "subject_name": "Jake", "subject_type": "character",
    "predicate": "eye_color", "value": "green",
    "kind": "physical", "confidence": 0.9, "quote": "His green eyes narrowed.",
}


def test_persist_inserts_new_fact():
    db = CanonFakeDB(existing=None)
    counts = persist_canon_facts(
        db, novel_id="n1", chapter_id="ch1", chapter_number=3,
        facts=[FACT], resolver=FakeResolver(),
    )
    assert counts == {"inserted": 1, "updated": 0, "contradictions": 0, "skipped": 0}
    assert any("INSERT INTO canon_facts" in q for q, _ in db.calls)


def test_persist_updates_unlocked_when_confidence_not_lower():
    db = CanonFakeDB(existing={"id": "f1", "value": "blue", "locked": False, "confidence": 0.5})
    counts = persist_canon_facts(
        db, novel_id="n1", chapter_id="ch1", chapter_number=3,
        facts=[FACT], resolver=FakeResolver(),
    )
    assert counts["updated"] == 1
    assert any("UPDATE canon_facts" in q for q, _ in db.calls)


def test_persist_flags_contradiction_of_locked_fact():
    db = CanonFakeDB(existing={"id": "f1", "value": "blue", "locked": True, "confidence": 1.0})
    counts = persist_canon_facts(
        db, novel_id="n1", chapter_id="ch1", chapter_number=3,
        facts=[FACT], resolver=FakeResolver(),
    )
    assert counts["contradictions"] == 1
    assert any("INSERT INTO continuity_flags" in q for q, _ in db.calls)
    assert not any("UPDATE canon_facts" in q for q, _ in db.calls)


def test_persist_skips_unknown_subject_type():
    db = CanonFakeDB()
    bad = dict(FACT, subject_type="spaceship")
    counts = persist_canon_facts(
        db, novel_id="n1", chapter_id="ch1", chapter_number=3,
        facts=[bad], resolver=FakeResolver(),
    )
    assert counts["skipped"] == 1
