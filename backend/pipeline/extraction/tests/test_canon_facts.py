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
