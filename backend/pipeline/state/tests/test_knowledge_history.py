"""A later belief replacement must not rewrite what was known earlier."""
from pipeline.critic.checks.knowledge_state_check import check_knowledge_state
from pipeline.db.story_records import capture_story_records
from pipeline.state.materializer import StateMaterializer
from reads.knowledge import list_knows_edges


def _learn(db, char, fact, chapter):
    return str(db.fetchval('INSERT INTO knows_edges(character_id,fact_description,learned_chapter) VALUES (%s,%s,%s) RETURNING id',
                           (char, fact, chapter), commit=True))


def _snapshots(db, char):
    return dict(db.fetchall('SELECT c.number, cs.knowledge FROM character_states cs JOIN chapters c ON c.id=cs.chapter_id WHERE cs.character_id=%s ORDER BY c.number', (char,)))


def test_supersession_preserves_cutoffs_and_historical_critic(db, seed_novel):
    seeded = seed_novel(db)
    novel, char = seeded['novel_id'], seeded['char_a_id']
    capture_story_records(db, novel, 1)
    old = _learn(db, char, 'The safe password is cedar', 1)
    StateMaterializer(db).materialize(novel)
    original = _snapshots(db, char)[1]
    claims = [{'character_id': char, 'fact_description': 'The safe password is cedar', 'learned_this_chapter': False}]
    assert check_knowledge_state(db, novel, 2, claims) == []
    new = _learn(db, char, 'The safe password is willow', 3)
    db.execute('UPDATE knows_edges SET superseded_by_id=%s WHERE id=%s', (new, old))
    for _ in range(2):  # full rebuild is semantically idempotent
        StateMaterializer(db).materialize(novel)
        snapshots = _snapshots(db, char)
        assert snapshots[1] == original
        assert 'The safe password is cedar' not in snapshots[3]
        assert 'The safe password is willow' in snapshots[3]
        for cutoff in (1, 2, 3):
            latest = max(n for n in snapshots if n <= cutoff)
            edges = list_knows_edges(db, novel, cutoff, char)
            assert set(snapshots[latest]) == {r['fact_description'] for r in edges}
        assert check_knowledge_state(db, novel, 2, claims) == []
    StateMaterializer(db).materialize(novel, 1)
    assert _snapshots(db, char)[1] == original
    StateMaterializer(db).materialize(novel)
    assert 'The safe password is willow' in _snapshots(db, char)[3]


def test_same_chapter_supersession_chain_and_duplicate_fact(db, seed_novel):
    seeded = seed_novel(db)
    novel, char = seeded['novel_id'], seeded['char_a_id']
    first = _learn(db, char, 'First belief', 1)
    middle = _learn(db, char, 'Second belief', 3)
    last = _learn(db, char, 'Third belief', 3)
    _learn(db, char, 'First belief', 1)  # independent assertion with same text
    db.execute('UPDATE knows_edges SET superseded_by_id=%s WHERE id=%s', (middle, first))
    db.execute('UPDATE knows_edges SET superseded_by_id=%s WHERE id=%s', (last, middle))
    StateMaterializer(db).materialize(novel)
    snapshots = _snapshots(db, char)
    assert 'First belief' in snapshots[1]
    assert 'First belief' in snapshots[3]
    assert 'Second belief' not in snapshots[3]
    assert 'Third belief' in snapshots[3]


def test_suffix_rewind_restores_superseded_belief_and_rolls_back_atomically(db, seed_novel):
    import pytest
    from pipeline.ingestion.ingest import delete_chapter_data
    seeded = seed_novel(db)
    novel, char = seeded['novel_id'], seeded['char_a_id']
    old = _learn(db, char, 'The safe password is cedar', 1)
    new = _learn(db, char, 'The safe password is willow', 3)
    db.execute('UPDATE knows_edges SET superseded_by_id=%s WHERE id=%s', (new, old))
    with pytest.raises(RuntimeError, match='replacement failed'):
        with db.session() as session:
            delete_chapter_data(session, novel_id=novel, chapter_number=3)
            StateMaterializer(session).materialize(novel, already_locked=True)
            raise RuntimeError('replacement failed')
    assert str(db.fetchval('SELECT superseded_by_id FROM knows_edges WHERE id=%s', (old,))) == new
    assert db.fetchval('SELECT count(*) FROM chapters WHERE novel_id=%s', (novel,)) == 3
    with db.session() as session:
        delete_chapter_data(session, novel_id=novel, chapter_number=3)
        StateMaterializer(session).materialize(novel, already_locked=True)
    assert db.fetchval('SELECT superseded_by_id FROM knows_edges WHERE id=%s', (old,)) is None
    assert 'The safe password is cedar' in _snapshots(db, char)[1]


def test_failed_rebuild_keeps_snapshots_edges_and_checkpoint_together(db, seed_novel, monkeypatch):
    import pytest
    seeded = seed_novel(db)
    novel, char = seeded['novel_id'], seeded['char_a_id']
    def projections():
        out = {}
        for table, predicate in [
            ('character_states', 'character_id IN (SELECT id FROM characters WHERE novel_id=%s)'),
            ('located_in_edges', 'entity_id IN (SELECT id FROM entities WHERE novel_id=%s)'),
            ('possesses_edges', 'character_id IN (SELECT id FROM characters WHERE novel_id=%s)'),
            ('materialized_state_runs', 'novel_id=%s'),
        ]:
            out[table] = db.fetchall(f'SELECT to_jsonb(t) FROM {table} t WHERE {predicate} ORDER BY id', (novel,))
        return out
    before = projections()
    chapter = db.fetchval("INSERT INTO chapters(novel_id,number,raw_text) VALUES (%s,4,'New learning and movement.') RETURNING id", (novel,), commit=True)
    _learn(db, char, 'Newly committed knowledge', 4)
    db.execute("INSERT INTO state_deltas(chapter_id,kind,subject_id,location_id,change) VALUES (%s,'location',%s,%s,'move')",
               (chapter, seeded['char_a_eid'], seeded['loc_a_id']))
    materializer = StateMaterializer(db)
    def fail_after_other_projections(*args):
        raise RuntimeError('projection write failed')
    monkeypatch.setattr(materializer, '_write_possession_edges', fail_after_other_projections)
    with pytest.raises(RuntimeError, match='projection write failed'):
        materializer.materialize(novel)
    assert projections() == before
    assert 'Newly committed knowledge' not in _snapshots(db, char).get(3, [])
    assert db.fetchval('SELECT max(number) FROM chapters WHERE novel_id=%s', (novel,)) == 4
    StateMaterializer(db).materialize(novel)
    assert 'Newly committed knowledge' in _snapshots(db, char)[4]
    assert db.fetchval('SELECT location_id FROM character_states WHERE character_id=%s AND chapter_id=%s', (char, chapter)) == db.fetchone('SELECT id FROM locations WHERE id=%s', (seeded['loc_a_id'],))[0]
