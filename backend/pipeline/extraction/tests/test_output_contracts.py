import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pipeline.extraction import extractor as module
from pipeline.extraction.extractor import ChapterExtractor, empty_extraction, merge_extractions
from pipeline.extraction.prompts import PASS_ORDER
from pipeline.extraction.validation import validate_pass
from testing.model_outputs import empty_pass


@pytest.mark.parametrize('name', PASS_ORDER)
def test_every_pass_accepts_complete_empty_findings_but_rejects_missing_keys(name):
    valid = empty_pass(name)
    assert validate_pass(name, valid) == valid
    broken = dict(valid)
    broken.pop(next(iter(broken)))
    with pytest.raises(ValueError):
        validate_pass(name, broken)


@pytest.mark.parametrize('payload', [
    {'unexpected': True}, {'events': [{}]}, {'events': 'not a list'},
    {'events': [dict(description='private story', event_type='made_up', impact_level='high',
                     involved_characters=[], involved_locations=[], involved_objects=[], involved_factions=[])]},
])
def test_invalid_pass_schema_retries_then_fails_without_logging_input(monkeypatch, payload, caplog):
    calls = []
    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)), finish_reason='stop')])
    monkeypatch.setattr(module, '_load_completion', lambda: completion)
    with pytest.raises(RuntimeError, match='invalid schema') as error:
        ChapterExtractor(use_mock=False)._run_llm_pass('events', 'private story', {})
    assert len(calls) == 3
    assert 'private story' not in str(error.value) + caplog.text


def test_schema_retry_accepts_repair_without_repeating_other_passes(monkeypatch):
    responses = iter([{'unexpected': True}, {'events': []}])
    monkeypatch.setattr(module, '_load_completion', lambda: lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(next(responses))))]))
    assert ChapterExtractor(use_mock=False)._run_llm_pass('events', 'story', {}) == {'events': []}


def test_conditional_delta_and_custom_entity_contracts():
    delta = dict(kind='possession', character_name='Aria', object_name=None, location_name=None,
                 change='gain', attribute=None, value=None, quote='Aria took it.')
    with pytest.raises(ValueError):
        validate_pass('state_deltas', {'state_deltas': [delta]})
    delta['object_name'] = 'Compass'
    assert validate_pass('state_deltas', {'state_deltas': [delta]})['state_deltas'][0]['object_name'] == 'Compass'
    custom = empty_pass('new_entities')
    with pytest.raises(ValueError, match='custom_entities'):
        validate_pass('new_entities', custom, [{'name': 'realm'}])
    custom['custom_entities'] = [{'name': 'Sky', 'type': 'realm', 'description': ''}]
    assert validate_pass('new_entities', custom, [{'name': 'realm'}]) == custom


def test_whole_chapter_summaries_keep_all_chunks_in_order():
    chunks = []
    for sentence in ['Aria learns the password.', 'Aria finds the compass.', 'Aria sleeps.']:
        part = empty_extraction()
        for key in ['summary_short', 'summary_medium', 'summary_long']:
            part[key] = sentence
        chunks.append(part)
    merged = merge_extractions(copy.deepcopy(chunks))
    for key in ['summary_short', 'summary_medium', 'summary_long']:
        assert merged[key].split('\n\n') == [c[key] for c in chunks]
    assert merge_extractions([chunks[0], chunks[0]])['summary_medium'] == chunks[0]['summary_medium']


def test_missing_sdk_never_implicitly_activates_mock(monkeypatch):
    from pipeline import embeddings
    from pipeline.extraction import canonicalizer
    monkeypatch.setattr(module, 'settings', replace(module.settings, use_mock_llm=False))
    monkeypatch.setattr(module, '_load_completion', lambda: None)
    monkeypatch.setattr(embeddings, 'settings', replace(embeddings.settings, use_mock_llm=False))
    monkeypatch.setattr(embeddings, '_load_embedding', lambda: None)
    monkeypatch.setattr(canonicalizer, 'settings', replace(canonicalizer.settings, use_mock_llm=False))
    monkeypatch.setattr(canonicalizer, '_load_completion', lambda: None)
    with pytest.raises(RuntimeError, match='LiteLLM'):
        ChapterExtractor()
    with pytest.raises(embeddings.EmbeddingError, match='LiteLLM'):
        embeddings.EmbeddingService()
    with pytest.raises(RuntimeError, match='LiteLLM'):
        canonicalizer.IntraExtractionDeduplicator()
    with pytest.raises(RuntimeError, match='LiteLLM'):
        canonicalizer.EntityCanonicalizer(None, novel_id='n')
    assert ChapterExtractor(use_mock=True).extract_chunk('Aria walks.', {})['summary']
    assert embeddings.EmbeddingService(use_mock=True).model_name == 'mock:hash'


def test_invalid_required_pass_leaves_existing_novel_unchanged(db, seed_novel, monkeypatch):
    from pipeline.pipeline import analyze_chapter
    seeded = seed_novel(db)
    novel = seeded['novel_id']
    before = db.fetchval('SELECT count(*) FROM entities WHERE novel_id=%s', (novel,))
    monkeypatch.setattr(module, '_load_completion', lambda: lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"unexpected": true}'), finish_reason='stop')]))
    with pytest.raises(RuntimeError, match='chapter_summary.*invalid schema'):
        analyze_chapter(novel_id=novel, chapter_number=4, raw_text='A new character appears.',
                        chapter_title=None, use_mock_llm=False, chunk_size=2000, chunk_overlap=200,
                        run_critic=False, db=db)
    assert db.fetchval('SELECT count(*) FROM chapters WHERE novel_id=%s', (novel,)) == 3
    assert db.fetchval('SELECT count(*) FROM entities WHERE novel_id=%s', (novel,)) == before


def test_assembled_medium_summary_is_stored_and_embedded_in_full(db, seed_novel):
    from pipeline.embeddings import EmbeddingService
    from pipeline.extraction.persist_extras import persist_multi_summaries
    seeded = seed_novel(db)
    first, last = empty_extraction(), empty_extraction()
    first['summary_medium'], last['summary_medium'] = 'Aria learns the password.', 'Aria sleeps.'
    merged = merge_extractions([first, last])
    seen = []
    class Capture(EmbeddingService):
        def embed_text(self, text):
            seen.append(text)
            return super().embed_text(text)
    persist_multi_summaries(db, chapter_id=seeded['chapter_ids'][0],
                            summary_short='', summary_medium=merged['summary_medium'],
                            summary_long='', embedder=Capture(use_mock=True))
    assert seen == ['Aria learns the password.\n\nAria sleeps.']
    assert db.fetchval('SELECT summary FROM chapters WHERE id=%s', (seeded['chapter_ids'][0],)) == seen[0]
