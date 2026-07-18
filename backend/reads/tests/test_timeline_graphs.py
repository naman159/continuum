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


def test_entity_graph_excludes_late_arriving_location_and_object_nodes(db, seed_novel):
    """loc B (Sable Archive) first-appears ch3; the node cutoff must apply to
    every typed entity with a first_appearance anchor, not just characters."""
    seeded = seed_novel(db)
    before = graphs_reads.entity_graph(db, seeded["novel_id"], up_to_chapter=2)
    assert seeded["loc_b_eid"] not in {n["id"] for n in before["nodes"]}
    after = graphs_reads.entity_graph(db, seeded["novel_id"], up_to_chapter=3)
    assert seeded["loc_b_eid"] in {n["id"] for n in after["nodes"]}


def test_relationship_with_null_from_chapter_is_cut_by_provenance_chapter(db, seed_novel):
    """A relationship whose extractor left from_chapter NULL must still be
    hidden below the chapter that asserted it (relationships.chapter_id)."""
    seeded = seed_novel(db)
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,'character','Cass') RETURNING id",
            (seeded["novel_id"],),
        )
        cass_eid = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO characters (novel_id, entity_id, name, first_appearance_chapter)"
            " VALUES (%s,%s,'Cass',1) RETURNING id",
            (seeded["novel_id"], cass_eid),
        )
        cur.execute(
            "INSERT INTO relationships (entity_a_id, entity_b_id, rel_type, from_chapter, chapter_id)"
            " VALUES (%s,%s,'debtor_of',NULL,%s)",
            (seeded["char_a_eid"], cass_eid, seeded["chapter_ids"][2]),
        )
    graph = graphs_reads.relationship_graph(db, seeded["novel_id"], up_to_chapter=2)
    assert not any(e["label"] == "debtor_of" for e in graph["edges"])
    graph3 = graphs_reads.relationship_graph(db, seeded["novel_id"], up_to_chapter=None)
    assert any(e["label"] == "debtor_of" for e in graph3["edges"])
