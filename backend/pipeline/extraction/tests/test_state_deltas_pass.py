"""The extractor emits typed state_deltas (replacing entity_deltas)."""

from __future__ import annotations

from pipeline.extraction.extractor import (
    ChapterExtractor,
    empty_extraction,
    merge_extractions,
)
from pipeline.extraction.prompts import PASS_ORDER, PASS_SCHEMAS


def test_pass_roster_swaps_entity_deltas_for_state_deltas():
    assert "state_deltas" in PASS_ORDER
    assert "entity_deltas" not in PASS_ORDER
    assert "state_deltas" in PASS_SCHEMAS
    assert "entity_deltas" not in PASS_SCHEMAS
    assert "state_deltas" in empty_extraction()
    assert "entity_deltas" not in empty_extraction()


def test_merge_preserves_chunk_order_of_deltas():
    a = empty_extraction()
    a["state_deltas"] = [
        {"kind": "possession", "character_name": "Aelric",
         "object_name": "silver dagger", "change": "gain", "quote": "q1"},
    ]
    b = empty_extraction()
    b["state_deltas"] = [
        {"kind": "possession", "character_name": "Aelric",
         "object_name": "silver dagger", "change": "loss", "quote": "q2"},
        {"kind": "status", "character_name": "Aelric",
         "attribute": "emotional_state", "value": "grieving", "quote": "q3"},
    ]
    merged = merge_extractions([a, b])
    kinds = [(d["kind"], d.get("change")) for d in merged["state_deltas"]]
    assert kinds == [("possession", "gain"), ("possession", "loss"), ("status", "update")]


def test_mock_extraction_emits_state_deltas():
    extractor = ChapterExtractor(use_mock=True)
    context = {"characters": [{"name": "Aelric"}], "locations": [], "open_threads": [],
               "recent_events": [], "custom_entities": {}}
    out = extractor.extract_chapter(
        chunks=["Aelric took the silver dagger and walked to Pellis Harbor."],
        context=context,
    )
    assert isinstance(out.get("state_deltas"), list)
    assert out["state_deltas"], "mock must emit at least one delta"
    for delta in out["state_deltas"]:
        assert delta["kind"] in {"possession", "location", "status"}
