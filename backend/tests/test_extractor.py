from __future__ import annotations

from pipeline.extraction.extractor import empty_extraction, merge_extractions, _normalize_extraction


def test_empty_extraction_has_relationship_and_dynamics_keys():
    result = empty_extraction()
    assert "relationship_updates" in result
    assert result["relationship_updates"] == []
    assert "dynamics_updates" in result
    assert result["dynamics_updates"] == []


def test_merge_extractions_dedupes_relationship_updates():
    a = empty_extraction()
    a["relationship_updates"] = [
        {"entity_a": "Alice", "entity_b": "Bob", "rel_type": "friend", "from_chapter": 1, "to_chapter": None, "notes": None}
    ]
    b = empty_extraction()
    b["relationship_updates"] = [
        {"entity_a": "Alice", "entity_b": "Bob", "rel_type": "friend", "from_chapter": 1, "to_chapter": None, "notes": None}
    ]
    merged = merge_extractions([a, b])
    assert len(merged["relationship_updates"]) == 1


def test_merge_extractions_dedupes_dynamics_updates():
    a = empty_extraction()
    a["dynamics_updates"] = [
        {"entity_a": "Alice", "entity_b": "Bob", "description": "tense"}
    ]
    b = empty_extraction()
    b["dynamics_updates"] = [
        {"entity_a": "Alice", "entity_b": "Bob", "description": "tense"}
    ]
    merged = merge_extractions([a, b])
    assert len(merged["dynamics_updates"]) == 1


def test_normalize_extraction_passes_through_new_fields():
    raw = {
        "relationship_updates": [
            {"entity_a": "Alice", "entity_b": "Bob", "rel_type": "friend", "from_chapter": 1, "to_chapter": None, "notes": None}
        ],
        "dynamics_updates": [
            {"entity_a": "Alice", "entity_b": "Bob", "description": "warm"}
        ],
    }
    result = _normalize_extraction(raw)
    assert len(result["relationship_updates"]) == 1
    assert len(result["dynamics_updates"]) == 1
