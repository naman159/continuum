"""Database regressions for the coordinator's history and failure boundaries."""
from __future__ import annotations

import pytest

from pipeline import pipeline as coordinator
from pipeline.db.history import capture_metadata, metadata_table
from pipeline.extraction.extractor import empty_extraction
from reads.characters import get_character_detail, list_characters
from reads.knowledge import list_canon_facts, list_knows_edges
from reads.threads import list_threads


@pytest.fixture
def novel(db):
    novel_id = str(db.fetchval("INSERT INTO novels (title) VALUES ('Architecture regression') RETURNING id", commit=True))
    yield novel_id
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def process(db, novel, number, text, **kwargs):
    return coordinator.analyze_chapter(
        novel_id=novel, chapter_number=number, raw_text=text, chapter_title=None,
        use_mock_llm=True, chunk_size=1000, chunk_overlap=100, db=db,
        run_critic=False, **kwargs,
    )


@pytest.fixture
def extracted(monkeypatch):
    contexts = []

    def extract(self, *, chunks, context, **kwargs):
        text = ' '.join(chunks)
        contexts.append((text, context))
        result = empty_extraction()
        result['summary'] = text
        result['new_entities']['characters'] = [{'name': 'Ada', 'description': text}]
        result['learnings'] = [{'character_name': 'Ada', 'fact_description': text, 'certainty': 'high'}]
        return result

    monkeypatch.setattr(coordinator.ChapterExtractor, 'extract_chapter', extract)
    return contexts


def test_knowledge_has_one_source_in_both_views(db, novel, extracted):
    process(db, novel, 1, 'The gate is open.')
    process(db, novel, 2, 'The road is flooded.')
    cid = list_characters(db, novel, 2)[0]['id']
    for cutoff in (1, 2):
        page = get_character_detail(db, novel, cid, cutoff)
        assertions = list_knows_edges(db, novel, cutoff, cid)
        assert set(page['current_state']['knowledge']) == {edge['fact_description'] for edge in assertions}
    assert db.fetchval("SELECT count(*) FROM state_deltas d JOIN chapters c ON c.id=d.chapter_id WHERE c.novel_id=%s AND d.kind='knowledge'", (novel,)) == 0


def test_alias_plan_rolls_back_with_failed_chapter(db, novel, extracted, monkeypatch):
    process(db, novel, 1, 'Ada arrived.')
    original = coordinator.EntityCanonicalizer.canonicalize

    def plan(self, **kwargs):
        original(self, **kwargs)
        row = self.db.fetchone("SELECT id FROM characters WHERE novel_id=%s", (novel,))
        self.alias_updates[('character', str(row[0]))] = ['The captain']
        return {}

    monkeypatch.setattr(coordinator.EntityCanonicalizer, 'canonicalize', plan)

    def fail(*args, **kwargs):
        raise RuntimeError('injected persistence failure')

    monkeypatch.setattr(coordinator, 'persist_scenes', fail)
    with pytest.raises(RuntimeError, match='injected'):
        process(db, novel, 2, 'The captain returned.')
    assert db.fetchval("SELECT aliases FROM characters WHERE novel_id=%s", (novel,)) == []
    assert db.fetchval("SELECT max(number) FROM chapters WHERE novel_id=%s", (novel,)) == 1


def test_retcon_reextracts_suffix_against_restored_context(db, novel, extracted):
    process(db, novel, 1, 'Original opening.')
    process(db, novel, 2, 'Dependent chapter.')
    old_ids = db.fetchall("SELECT id FROM chapters WHERE novel_id=%s ORDER BY number", (novel,))
    extracted.clear()
    result = process(db, novel, 1, 'Corrected opening.', replace=True)
    assert result['rebuilt_chapters'] == [1, 2]
    assert [text for text, _ in extracted] == ['Corrected opening.', 'Dependent chapter.']
    assert extracted[0][1]['characters'] == []
    assert extracted[1][1]['characters'][0]['name'] == 'Ada'
    assert db.fetchall("SELECT id FROM chapters WHERE novel_id=%s ORDER BY number", (novel,)) != old_ids
    cid = list_characters(db, novel, 2)[0]['id']
    knowledge = get_character_detail(db, novel, cid, 2)['current_state']['knowledge']
    assert 'Corrected opening.' in knowledge and 'Original opening.' not in knowledge


def test_retcon_failure_preserves_original_entire_suffix(db, novel, extracted, monkeypatch):
    process(db, novel, 1, 'Original opening.')
    process(db, novel, 2, 'Dependent chapter.')
    before = db.fetchall("SELECT id,raw_text FROM chapters WHERE novel_id=%s ORDER BY number", (novel,))
    original = coordinator.ChapterExtractor.extract_chapter

    def fail_second(self, **kwargs):
        if kwargs['chunks'] == ['Dependent chapter.']:
            raise RuntimeError('second chapter failed')
        return original(self, **kwargs)

    monkeypatch.setattr(coordinator.ChapterExtractor, 'extract_chapter', fail_second)
    with pytest.raises(RuntimeError, match='second chapter'):
        process(db, novel, 1, 'Corrected opening.', replace=True)
    assert db.fetchall("SELECT id,raw_text FROM chapters WHERE novel_id=%s ORDER BY number", (novel,)) == before
    cid = list_characters(db, novel, 2)[0]['id']
    knowledge = get_character_detail(db, novel, cid, 2)['current_state']['knowledge']
    assert 'Original opening.' in knowledge and 'Corrected opening.' not in knowledge


def test_metadata_reads_preserve_alias_canon_and_thread_history(db, novel, extracted):
    process(db, novel, 1, 'Ada arrived.')
    eid = db.fetchval("SELECT entity_id FROM characters WHERE novel_id=%s", (novel,))
    with db.session() as session:
        session.execute("INSERT INTO canon_facts(novel_id,kind,subject_entity_id,predicate,value,source_chapter) VALUES(%s,'other',%s,'role','traveler',1)", (novel,eid))
        session.execute("INSERT INTO plot_threads(novel_id,title,description,status,opened_chapter) VALUES(%s,'Journey','Find a route','open',1)", (novel,))
        capture_metadata(session, novel, 1)
    process(db, novel, 2, 'Ada returned.')
    with db.session() as session:
        session.execute("UPDATE characters SET aliases=ARRAY['Captain'],description='Promoted captain' WHERE novel_id=%s", (novel,))
        session.execute("UPDATE canon_facts SET value='captain',source_chapter=2 WHERE novel_id=%s", (novel,))
        session.execute("UPDATE plot_threads SET description='Route found',status='closed',closed_chapter=2 WHERE novel_id=%s", (novel,))
        capture_metadata(session, novel, 2)
    assert list_characters(db, novel, 1)[0]['aliases'] == []
    assert list_characters(db, novel, 2)[0]['aliases'] == ['Captain']
    assert list_canon_facts(db, novel, 1, False)[0]['value'] == 'traveler'
    assert list_canon_facts(db, novel, 2, False)[0]['value'] == 'captain'
    assert list_threads(db, novel, 1)[0]['description'] == 'Find a route'
    assert list_threads(db, novel, 2)[0]['description'] == 'Route found'


def test_admission_and_order_fail_before_extraction(db, novel, extracted):
    with db.novel_lock(novel):
        with pytest.raises(ValueError, match='already being processed'):
            process(db, novel, 1, 'Opening.')
    with pytest.raises(ValueError, match='expected chapter 1'):
        process(db, novel, 3, 'Out of order.')
    assert extracted == []


def test_unresolved_enrichment_is_reported(db, novel, extracted, monkeypatch):
    original = coordinator.ChapterExtractor.extract_chapter

    def unresolved(self, **kwargs):
        result = original(self, **kwargs)
        result['learnings'].append({'character_name': 'Nobody', 'fact_description': 'unresolved'})
        return result

    monkeypatch.setattr(coordinator.ChapterExtractor, 'extract_chapter', unresolved)
    result = process(db, novel, 1, 'Ada arrived.')
    assert result['enrichment']['knowledge'] == {'extracted': 2, 'written': 1}
    assert any('Nobody' in warning for warning in result['enrichment']['warnings'])


def test_metadata_sql_rejects_identifiers_and_values():
    with pytest.raises(ValueError):
        metadata_table('chapters; DELETE FROM novels', 'bad', 1)
    with pytest.raises(ValueError):
        metadata_table('characters', "' OR true", 1)


def test_database_error_rolls_back_instead_of_reporting_success(db, novel, extracted, monkeypatch):
    from psycopg.errors import DivisionByZero

    def broken_scene(session, **kwargs):
        session.fetchval('SELECT 1 / 0')

    monkeypatch.setattr(coordinator, 'persist_scenes', broken_scene)
    with pytest.raises(DivisionByZero):
        process(db, novel, 1, 'Ada arrived.')
    assert db.fetchval('SELECT count(*) FROM chapters WHERE novel_id=%s', (novel,)) == 0
    assert db.fetchval('SELECT count(*) FROM characters WHERE novel_id=%s', (novel,)) == 0
    assert db.fetchval('SELECT count(*) FROM metadata_versions WHERE novel_id=%s', (novel,)) == 0


def test_entity_repair_survives_rewind_and_keeps_knowledge(db, novel, extracted):
    from pipeline.db.entity_merge import merge_entities

    process(db, novel, 1, 'Ada arrived.')
    target = str(db.fetchval('SELECT entity_id FROM characters WHERE novel_id=%s', (novel,)))
    duplicate = str(db.fetchval("INSERT INTO entities(novel_id,entity_type,name) VALUES(%s,'character','The captain') RETURNING id", (novel,), commit=True))
    typed_duplicate = db.fetchval("INSERT INTO characters(novel_id,entity_id,name,first_appearance_chapter) VALUES(%s,%s,'The captain',1) RETURNING id", (novel, duplicate), commit=True)
    db.execute("INSERT INTO knows_edges(character_id,fact_description,learned_chapter) VALUES(%s,'The route is closed.',1)", (typed_duplicate,))
    capture_metadata(db, novel, 1)
    process(db, novel, 2, 'Ada returned.')
    merge_entities(db, novel_id=novel, source_entity_id=duplicate, target_entity_id=target)
    assert len(list_characters(db, novel, 1)) == 1
    cid = list_characters(db, novel, 2)[0]['id']
    assert 'The route is closed.' in get_character_detail(db, novel, cid, 2)['current_state']['knowledge']
    process(db, novel, 2, 'Ada returned again.', replace=True)
    assert len(list_characters(db, novel, 1)) == 1
    assert db.fetchval('SELECT id FROM entities WHERE id=%s', (duplicate,)) is None


def test_append_repairs_a_truncated_projection_before_context(db, novel, extracted):
    from pipeline.state.materializer import StateMaterializer

    process(db, novel, 1, 'Ada arrived.')
    process(db, novel, 2, 'Ada returned.')
    StateMaterializer(db).materialize(novel, through_chapter=1)
    extracted.clear()
    process(db, novel, 3, 'Ada departed.')
    assert extracted[0][1]['characters'][0]['last_chapter'] == 2
