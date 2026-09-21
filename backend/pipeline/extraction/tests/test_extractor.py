from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline.extraction import extractor as extractor_module
from pipeline.extraction.extractor import empty_extraction, merge_extractions, _normalize_extraction


def test_provider_failure_stops_extraction_instead_of_returning_empty(monkeypatch):
    def unavailable(**kwargs):
        raise RuntimeError("Gemini quota exhausted")

    monkeypatch.setattr(extractor_module, "_load_completion", lambda: unavailable)
    with pytest.raises(RuntimeError, match="chapter_summary.*Gemini quota exhausted"):
        extractor_module.ChapterExtractor(use_mock=False).extract_chunk("Real prose.", {})


@pytest.mark.parametrize("content", [None, "", "not JSON", "{}", "[]"])
def test_empty_or_invalid_provider_response_stops_extraction(monkeypatch, content):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(extractor_module, "_load_completion", lambda: completion)
    with pytest.raises(RuntimeError, match="chapter_summary.*empty or invalid JSON.*attempt=3/3"):
        extractor_module.ChapterExtractor(use_mock=False).extract_chunk("Real prose.", {})
    assert len(calls) == 3


def _response(content, finish_reason="stop", refusal=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, refusal=refusal),
        finish_reason=finish_reason,
    )])


@pytest.mark.parametrize("first_response", [
    _response(None),
    _response('{"learnings": ['),
    _response('{"learnings": []}', finish_reason="length"),
    SimpleNamespace(choices=[]),
])
def test_knowledge_pass_recovers_from_unusable_output(monkeypatch, first_response):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return first_response if len(calls) == 1 else _response('{"learnings": []}')

    monkeypatch.setattr(extractor_module, "_load_completion", lambda: completion)
    extractor = extractor_module.ChapterExtractor(use_mock=False)
    assert extractor._run_llm_pass("knowledge_state_deltas", "Real prose.", {}) == {"learnings": []}
    assert len(calls) == 2
    assert "use [] for lists with no findings" in calls[1]["messages"][1]["content"]


@pytest.mark.parametrize("content", [
    '{"learnings": []}',
    '```json\n{"learnings": []}\n```',
    [{"type": "text", "text": '{"learnings": []}'}],
    [{"type": "text", "text": '{"learnings":'}, {"type": "text", "text": ' []}'}],
    [SimpleNamespace(type="text", text='{"learnings": []}')],
])
def test_valid_empty_findings_and_text_blocks_succeed_without_retry(monkeypatch, content):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _response(content)

    monkeypatch.setattr(extractor_module, "_load_completion", lambda: completion)
    extractor = extractor_module.ChapterExtractor(use_mock=False)
    assert extractor._run_llm_pass("knowledge_state_deltas", "Real prose.", {}) == {"learnings": []}
    assert len(calls) == 1


@pytest.mark.parametrize("finish_reason,refusal", [("content_filter", None), ("stop", "refused")])
def test_refusals_are_not_retried(monkeypatch, finish_reason, refusal):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _response(None, finish_reason, refusal)

    monkeypatch.setattr(extractor_module, "_load_completion", lambda: completion)
    with pytest.raises(RuntimeError, match="knowledge_state_deltas.*refused or filtered"):
        extractor_module.ChapterExtractor(use_mock=False)._run_llm_pass("knowledge_state_deltas", "prose", {})
    assert len(calls) == 1


def test_retry_does_not_repeat_completed_passes(monkeypatch):
    from pipeline.extraction.prompts import PASS_ORDER

    calls = []

    def completion(**kwargs):
        system = kwargs["messages"][0]["content"]
        name = next(name for name in PASS_ORDER if f"Extraction pass: {name}\n" in system)
        calls.append(name)
        if name == "knowledge_state_deltas" and calls.count(name) == 1:
            return _response("")
        return _response('{"learnings": []}' if name == "knowledge_state_deltas" else '{"summary": "A summary."}')

    monkeypatch.setattr(extractor_module, "_load_completion", lambda: completion)
    extractor_module.ChapterExtractor(use_mock=False).extract_chunk("Real prose.", {})
    expected = list(PASS_ORDER)
    expected.insert(expected.index("knowledge_state_deltas"), "knowledge_state_deltas")
    assert calls == expected


def test_truncation_diagnostics_exclude_response_text(monkeypatch, caplog):
    monkeypatch.setattr(extractor_module, "_load_completion", lambda: (
        lambda **kwargs: _response('{"summary": "private story"}', "length")
    ))
    with pytest.raises(RuntimeError, match="truncated.*finish_reason=length.*attempt=3/3") as error:
        extractor_module.ChapterExtractor(use_mock=False).extract_chunk("Real prose.", {})
    assert "private story" not in str(error.value)
    assert "private story" not in caplog.text


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


def test_chunk_merge_keeps_relationship_endings_and_directions():
    first, second = empty_extraction(), empty_extraction()
    active = {"entity_a": "Alice", "entity_b": "Bob", "rel_type": "mentor",
              "symmetric": False, "from_chapter": 1, "to_chapter": None}
    ended = {**active, "to_chapter": 3}
    reversed_edge = {**active, "entity_a": "Bob", "entity_b": "Alice"}
    first["relationship_updates"] = [active]
    second["relationship_updates"] = [dict(active), ended, reversed_edge]
    assert merge_extractions([first, second])["relationship_updates"] == [active, ended, reversed_edge]


def test_chunk_merge_collapses_only_explicitly_mutual_reversed_pairs():
    extraction = empty_extraction()
    mutual = {"entity_a": "Alice", "entity_b": "Bob", "rel_type": "friend", "symmetric": True}
    extraction["relationship_updates"] = [mutual, {**mutual, "entity_a": "Bob", "entity_b": "Alice"}]
    assert merge_extractions([extraction])["relationship_updates"] == [mutual]
