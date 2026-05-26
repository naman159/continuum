from pipeline.extraction.prompts import build_system_prompt, build_user_prompt
from pipeline.extraction.extractor import empty_extraction, _normalize_extraction, merge_extractions


def test_build_system_prompt_no_custom_types():
    prompt = build_system_prompt("new_entities")
    assert "custom_entities" not in prompt


def test_build_system_prompt_with_custom_types():
    custom_types = [
        {"name": "realm", "description": "A distinct universe."},
        {"name": "power_system", "description": "Named ability system."},
    ]
    prompt = build_system_prompt("new_entities", custom_entity_types=custom_types)
    assert "custom_entities" in prompt
    assert "realm" in prompt
    assert "power_system" in prompt


def test_normalize_extraction_custom_entities():
    raw = {
        "custom_entities": [
            {"name": "The 93rd Universe", "type": "realm", "description": "A dimension."},
        ]
    }
    result = _normalize_extraction(raw)
    assert result["custom_entities"] == [
        {"name": "The 93rd Universe", "type": "realm", "description": "A dimension."}
    ]


def test_empty_extraction_has_custom_entities():
    result = empty_extraction()
    assert "custom_entities" in result
    assert result["custom_entities"] == []


def test_merge_extractions_custom_entities():
    e1 = empty_extraction()
    e1["custom_entities"] = [{"name": "The 93rd Universe", "type": "realm", "description": "A dimension."}]
    e2 = empty_extraction()
    e2["custom_entities"] = [{"name": "The System", "type": "power_system", "description": "Ability system."}]
    merged = merge_extractions([e1, e2])
    names = {e["name"] for e in merged["custom_entities"]}
    assert names == {"The 93rd Universe", "The System"}


def test_merge_extractions_deduplicates_custom_entities():
    e1 = empty_extraction()
    e1["custom_entities"] = [{"name": "The 93rd Universe", "type": "realm", "description": "A dimension."}]
    e2 = empty_extraction()
    e2["custom_entities"] = [{"name": "The 93rd Universe", "type": "realm", "description": "Same thing."}]
    merged = merge_extractions([e1, e2])
    assert len(merged["custom_entities"]) == 1
