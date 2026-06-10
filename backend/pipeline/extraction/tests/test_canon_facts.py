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
