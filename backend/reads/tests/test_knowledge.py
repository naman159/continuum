from __future__ import annotations

from reads import knowledge as knowledge_reads


def test_canon_facts_cutoff(db, seed_novel):
    seeded = seed_novel(db)  # factory canon fact: source_chapter=2
    assert knowledge_reads.list_canon_facts(db, seeded["novel_id"], up_to_chapter=1, locked_only=False) == []
    assert knowledge_reads.list_canon_facts(db, seeded["novel_id"], up_to_chapter=2, locked_only=False)


def test_edges_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    for fn in (knowledge_reads.list_location_edges, knowledge_reads.list_possession_edges):
        rows = fn(db, seeded["novel_id"], up_to_chapter=1, active_only=False)
        assert all(r["since_chapter"] <= 1 for r in rows)
