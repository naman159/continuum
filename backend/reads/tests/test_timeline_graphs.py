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


def test_entity_graph_excludes_late_arriving_character_and_edges(db, seed_novel):
    """char B (Borin Thale) first-appears ch3, and the seed factory's
    char-A/char-B relationship edge is also anchored at from_chapter=3.
    Below that cutoff both the node and the edge must be absent; at the
    cutoff both are present."""
    seeded = seed_novel(db)

    before = graphs_reads.entity_graph(db, seeded["novel_id"], up_to_chapter=2)
    before_node_ids = {n["id"] for n in before["nodes"]}
    assert seeded["char_b_eid"] not in before_node_ids
    for edge in before["edges"]:
        assert seeded["char_b_eid"] not in (edge["from"], edge["to"])

    after = graphs_reads.entity_graph(db, seeded["novel_id"], up_to_chapter=3)
    after_node_ids = {n["id"] for n in after["nodes"]}
    assert seeded["char_b_eid"] in after_node_ids
    assert any(
        {edge["from"], edge["to"]} == {seeded["char_a_eid"], seeded["char_b_eid"]}
        for edge in after["edges"]
    )
