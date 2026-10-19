"""Compare stored snapshots with a status-only projection on synthetic novels.

Run only against an initialized disposable database, with dotenv disabled:
  PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m benchmarks.snapshot_projection \
      --database-url postgresql://127.0.0.1:55439/continuum_review --output ../docs/snapshot-comparison.json

Both candidates persist real tables with comparable indexes/FKs and replay the
same source assertions. The alternative retains existing replay and edge writes;
this measures a conservative storage change, not a new optimized replay engine.
Warm, single-session timings are not concurrent endpoint latency or production
capacity. Every fixture and scratch table is removed within its transaction.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import uuid
from pathlib import Path

from pipeline.db.client import DBClient
from pipeline.state.materializer import StateMaterializer


def _time(fn, repeats=15):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return {'median_ms': round(statistics.median(samples), 3),
            'p95_ms': round(sorted(samples)[math.ceil(len(samples) * .95) - 1], 3),
            'samples': repeats}


def run_case(db, characters, chapters):
    novel = str(uuid.uuid4())
    scratch = 'status_bench_' + uuid.uuid4().hex[:12]
    with db.session() as s:
        s.execute('INSERT INTO novels(id,title) VALUES (%s,%s)', (novel, 'Synthetic projection benchmark'))
        chapter_ids = [str(uuid.uuid4()) for _ in range(chapters)]
        char_ids = [str(uuid.uuid4()) for _ in range(characters)]
        entity_ids = [str(uuid.uuid4()) for _ in char_ids]
        location_entities = [str(uuid.uuid4()) for _ in range(2)]
        locations = [str(uuid.uuid4()) for _ in range(2)]
        with s.transaction() as cur:
            cur.executemany('INSERT INTO chapters(id,novel_id,number,raw_text) VALUES (%s,%s,%s,%s)',
                            [(cid, novel, i + 1, 'Synthetic chapter.') for i, cid in enumerate(chapter_ids)])
            cur.executemany("INSERT INTO entities(id,novel_id,entity_type,name) VALUES (%s,%s,'character',%s)",
                            [(eid, novel, f'Character {i}') for i, eid in enumerate(entity_ids)])
            cur.executemany('INSERT INTO characters(id,entity_id,novel_id,name,first_appearance_chapter) VALUES (%s,%s,%s,%s,1)',
                            [(cid, eid, novel, f'Character {i}') for i, (cid, eid) in enumerate(zip(char_ids, entity_ids))])
            for i, (eid, lid) in enumerate(zip(location_entities, locations)):
                cur.execute("INSERT INTO entities(id,novel_id,entity_type,name) VALUES (%s,%s,'location',%s)", (eid, novel, f'Place {i}'))
                cur.execute('INSERT INTO locations(id,entity_id,novel_id,name) VALUES (%s,%s,%s,%s)', (lid, eid, novel, f'Place {i}'))
            deltas, learnings = [], []
            for i, (cid, eid) in enumerate(zip(char_ids, entity_ids)):
                knowledge_ids = []
                for n, chapter in enumerate(chapter_ids, 1):
                    deltas.append((chapter, eid, locations[n % 2], 0))
                    if n == chapters // 2:
                        deltas.append((chapter, eid, locations[(n + 1) % 2], 1))
                    kid = str(uuid.uuid4()); knowledge_ids.append(kid)
                    learnings.append((kid, cid, f'Character {i} learned the distinct route secret in chapter {n}; the hidden gate opens after the evening bell.', n))
                # Insert all assertion rows before creating replacement pointers.
                cur.executemany('INSERT INTO knows_edges(id,character_id,fact_description,learned_chapter) VALUES (%s,%s,%s,%s)', learnings[-chapters:])
                cur.execute('UPDATE knows_edges SET superseded_by_id=%s WHERE id=%s', (knowledge_ids[chapters // 2], knowledge_ids[0]))
            cur.executemany("INSERT INTO state_deltas(chapter_id,subject_id,location_id,ordinal,kind,change) VALUES (%s,%s,%s,%s,'location','move')", deltas)
        s.execute(f'CREATE TABLE {scratch} (LIKE character_states INCLUDING ALL)')
        s.execute(f'ALTER TABLE {scratch} DROP COLUMN location_id, DROP COLUMN knowledge')
        s.execute(f'ALTER TABLE {scratch} ADD FOREIGN KEY(character_id) REFERENCES characters(id) ON DELETE CASCADE, ADD FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE')

        class StatusOnly(StateMaterializer):
            def _write_character_states(self, cur, novel_id, snapshots):
                cur.execute(f'DELETE FROM {scratch} WHERE character_id IN (SELECT id FROM characters WHERE novel_id=%s)', (novel_id,))
                for snap in snapshots:
                    cur.execute(f'INSERT INTO {scratch}(character_id,chapter_id,emotional_state,goals,physical_state,appearance,notes) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                                (snap.character_id, snap.chapter_id, snap.emotional_state, snap.goals, snap.physical_state, snap.appearance, snap.notes))
                return len(snapshots)

        current, alternative = StateMaterializer(s), StatusOnly(s)
        current.materialize(novel, already_locked=True)
        alternative.materialize(novel, already_locked=True)
        stored_sql = '''SELECT ch.number, cs.location_id, cs.knowledge FROM character_states cs
                        JOIN chapters ch ON ch.id=cs.chapter_id WHERE cs.character_id=%s AND ch.number<=%s ORDER BY ch.number'''
        assembled_sql = f'''SELECT ch.number, loc.location_id,
                    ARRAY(SELECT DISTINCT k.fact_description FROM knows_edges k
                           WHERE k.character_id=cs.character_id AND k.learned_chapter<=ch.number
                             AND NOT EXISTS (SELECT 1 FROM knows_edges newer WHERE newer.id=k.superseded_by_id AND newer.learned_chapter<=ch.number)) AS knowledge
                    FROM {scratch} cs JOIN chapters ch ON ch.id=cs.chapter_id
                    JOIN characters c ON c.id=cs.character_id
                    LEFT JOIN LATERAL (SELECT le.location_id FROM located_in_edges le
                         WHERE le.entity_id=c.entity_id AND le.since_chapter<=ch.number
                           AND (le.until_chapter IS NULL OR le.until_chapter>=ch.number)
                           AND NOT EXISTS (SELECT 1 FROM located_in_edges newer WHERE newer.id=le.superseded_by_id AND newer.since_chapter<=ch.number)
                         ORDER BY le.since_chapter DESC LIMIT 1) loc ON true
                    WHERE cs.character_id=%s AND ch.number<=%s ORDER BY ch.number'''
        def normalized(rows):
            return [(n, str(loc), set(knowledge or [])) for n, loc, knowledge in rows]
        for cid in char_ids:
            assert normalized(s.fetchall(stored_sql, (cid, chapters))) == normalized(s.fetchall(assembled_sql, (cid, chapters)))
        read = {'stored': _time(lambda: s.fetchall(stored_sql, (char_ids[0], chapters))),
                'assembled': _time(lambda: s.fetchall(assembled_sql, (char_ids[0], chapters)))}
        rebuild = {'stored': _time(lambda: current.materialize(novel, already_locked=True), 3),
                   'status_only': _time(lambda: alternative.materialize(novel, already_locked=True), 3)}
        sizes = {}
        for label, table in [('stored', 'character_states'), ('status_only', scratch)]:
            sizes[label] = int(s.fetchval(f'SELECT COALESCE(sum(pg_column_size(cs)),0) FROM {table} cs JOIN characters c ON c.id=cs.character_id WHERE c.novel_id=%s', (novel,)))
        version = s.fetchval('SHOW server_version')
        s.execute(f'DROP TABLE {scratch}')
        s.execute('DELETE FROM novels WHERE id=%s', (novel,))
    return {'characters': characters, 'chapters': chapters, 'snapshots': characters * chapters,
            'postgresql': version, 'all_historical_rows_agree': True, 'logical_row_bytes': sizes,
            'one_character_full_history_read': read, 'full_projection_rebuild': rebuild}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True, help='An initialized disposable database, never a manuscript database')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with DBClient(dsn=args.database_url) as db:
        results = [run_case(db, 8, 12), run_case(db, 24, 48)]
    report = {'workload': 'synthetic, warm reads, one session; one learning and location change per character/chapter; includes supersession and same-chapter moves',
              'storage': 'logical row bytes, not heap/index/TOAST allocation',
              'rebuild': 'same replay and edge persistence; alternative stores only status fields with comparable indexes/FKs',
              'cases': results}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
