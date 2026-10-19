from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from pipeline.critic.adapter import build_draft_chapter, extract_draft_claims

CHAR_ID = str(uuid.uuid4())
CHAR_ENTITY_ID = str(uuid.uuid4())
OBJ_ID = str(uuid.uuid4())


class ClaimsFakeDB:
    """Read-only name lookups: knows Jake (character) and Knife (object)."""

    def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
        name = str(params[1]).lower() if params and len(params) > 1 else ""
        if ("FROM characters" in query or "public.characters" in query) and name == "jake":
            return (CHAR_ID, CHAR_ENTITY_ID)
        if ("FROM objects" in query or "public.objects" in query) and name == "knife":
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
         "source_type": "inference", "learned_this_chapter": False, "quote": "Jake knew the ledger was forged"},
    ],
    "possession_claims": [{"character_name": "Jake", "object_name": "Knife", "quote": "his knife"}],
    "events": [{"description": "Jake confronts Sara", "event_type": "conflict"}],
}


def test_build_draft_chapter_resolves_names_read_only():
    draft = build_draft_chapter(
        ClaimsFakeDB(), novel_id="00000000-0000-0000-0000-000000000001", chapter_number=5, text="his green eyes z Jake knew the ledger was forged his knife",
        raw_claims=RAW,
    )
    assert draft.mentions == [
        {"entity_id": CHAR_ENTITY_ID, "predicate": "eye_color",
         "claimed_value": "green", "quote": "his green eyes"}
    ]
    assert draft.knowledge_claims[0]["character_id"] == CHAR_ID
    assert draft.possession_claims[0]["object_id"] == OBJ_ID
    assert draft.events[0]["description"] == "Jake confronts Sara"


def test_extract_draft_claims_parses_llm_json():
    def fake_completion(**kwargs):
        msg = SimpleNamespace(content=json.dumps(RAW))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    out = extract_draft_claims("his green eyes z Jake knew the ledger was forged his knife", use_mock=False, completion_fn=fake_completion)
    assert out["mentions"][0]["entity_name"] == "Jake"


def test_extract_draft_claims_mock_is_empty():
    out = extract_draft_claims("prose", use_mock=True)
    assert out == {"mentions": [], "knowledge_claims": [],
                   "possession_claims": [], "events": []}


def test_extract_draft_claims_bad_json_raises_in_real_mode():
    """Empty claims would make the critic pass vacuously — real-mode failures
    must surface instead of letting unchecked drafts through the ingest gate."""
    import pytest

    def fake_completion(**kwargs):
        msg = SimpleNamespace(content="not json")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    with pytest.raises(RuntimeError, match="unparseable JSON"):
        extract_draft_claims("prose", use_mock=False, completion_fn=fake_completion)


def test_parseable_but_malformed_claims_cannot_pass_as_empty():
    import pytest
    for malformed in ({"unexpected": True}, {**RAW, "mentions": None}, {**RAW, "events": ["not a claim"]}):
        def completion(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(malformed)))])
        with pytest.raises(RuntimeError, match="missing or malformed"):
            extract_draft_claims("prose", use_mock=False, completion_fn=completion)


def test_claim_resolution_reports_coverage_instead_of_silently_dropping():
    draft = build_draft_chapter(ClaimsFakeDB(), novel_id='00000000-0000-0000-0000-000000000001',
                                chapter_number=5, text='his green eyes z Jake knew the ledger was forged his knife', raw_claims=RAW)
    assert draft.unresolved_claims == [{'kind': 'mention', 'name': 'Nobody'}]
    assert draft.claim_counts == {'extracted': 5, 'resolved_entity_claims': 3, 'unresolved': 1, 'events': 1}


def test_claim_contract_rejects_truncation_refusal_missing_flags_and_fake_evidence():
    import copy
    import pytest
    bad_flag = copy.deepcopy(RAW)
    bad_flag['knowledge_claims'][0].pop('learned_this_chapter')
    fake_quote = copy.deepcopy(RAW)
    fake_quote['mentions'][0]['quote'] = 'Invented quote'
    for payload, reason, refusal in [
        (RAW, 'length', None), (RAW, 'content_filter', None), (RAW, 'stop', 'No'),
        ({**RAW, 'mentions': [{}]}, 'stop', None), (bad_flag, 'stop', None), (fake_quote, 'stop', None),
    ]:
        def completion(**kw):
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason,
                message=SimpleNamespace(content=json.dumps(payload), refusal=refusal))])
        with pytest.raises(RuntimeError):
            extract_draft_claims('his green eyes z Jake knew the ledger was forged his knife', use_mock=False, completion_fn=completion)


def test_empty_claims_and_text_blocks_are_valid_without_inventing_coverage():
    empty = {key: [] for key in RAW}
    def completion(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            content=[{'type': 'text', 'text': json.dumps(empty)}]))])
    result = extract_draft_claims('The rain fell.', use_mock=False, completion_fn=completion)
    draft = build_draft_chapter(ClaimsFakeDB(), novel_id='00000000-0000-0000-0000-000000000001',
                                chapter_number=1, text='The rain fell.', raw_claims=result)
    assert draft.claim_counts['extracted'] == draft.claim_counts['unresolved'] == 0


def test_claims_missing_sdk_is_an_error_with_mock_disabled(monkeypatch):
    from dataclasses import replace
    from pipeline.critic import adapter
    import pytest
    monkeypatch.setattr(adapter, 'settings', replace(adapter.settings, use_mock_llm=False))
    monkeypatch.setattr(adapter, '_load_completion', lambda: None)
    with pytest.raises(RuntimeError, match='LiteLLM unavailable'):
        adapter.extract_draft_claims('Rain.')
