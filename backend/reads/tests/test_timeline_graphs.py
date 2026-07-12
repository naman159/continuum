from __future__ import annotations

from reads import graphs as graphs_reads
from reads import timeline as timeline_reads


def test_timeline_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    rows = timeline_reads.list_timeline(db, seeded["novel_id"], up_to_chapter=1)
    assert rows and all(r["chapter_number"] <= 1 for r in rows)


def test_relationship_graph_single_impl_serves_both_surfaces(db, seed_novel):
    seeded = seed_novel(db)
    graph = graphs_reads.relationship_graph(db, seeded["novel_id"], up_to_chapter=3)
    assert set(graph.keys()) >= {"nodes", "edges"}
    assert graph["nodes"]


def test_shared_dynamics_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    assert graphs_reads.list_shared_dynamics(db, seeded["novel_id"], up_to_chapter=0) == []
