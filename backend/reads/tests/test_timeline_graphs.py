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


def test_an_established_relationship_persists_until_it_is_broken(db, seed_novel):
    """Raising the cutoff must never remove a node or an edge.

    `to_chapter` means the relationship STOPPED being true — not "this is the
    chapter I read it in". An extractor that writes to_chapter = from_chapter
    tells the graph the relationship ended the moment it began, so it vanishes
    one chapter later and takes any character whose only edge it was with it
    (nodes are derived from edges). On a real Pride and Prejudice ingest that
    dropped four characters between chapter 1 and chapter 2, including a
    married couple the book never separates.
    """
    seeded = seed_novel(db)
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,'character','Wilhelmina') RETURNING id",
            (seeded["novel_id"],),
        )
        spouse_eid = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO characters (novel_id, entity_id, name, first_appearance_chapter)"
            " VALUES (%s,%s,'Wilhelmina',1)",
            (seeded["novel_id"], spouse_eid),
        )
        cur.execute(
            "INSERT INTO relationships (entity_a_id, entity_b_id, rel_type, from_chapter, to_chapter)"
            " VALUES (%s,%s,'spouse',1,NULL)",
            (seeded["char_a_eid"], spouse_eid),
        )

    at_1 = graphs_reads.relationship_graph(db, seeded["novel_id"], up_to_chapter=1)
    at_3 = graphs_reads.relationship_graph(db, seeded["novel_id"], up_to_chapter=3)

    assert any(e["label"] == "spouse" for e in at_1["edges"])
    assert any(e["label"] == "spouse" for e in at_3["edges"]), (
        "a marriage established in ch1 that nothing ends must still hold at ch3"
    )
    assert {n["id"] for n in at_1["nodes"]} <= {n["id"] for n in at_3["nodes"]}, (
        "raising the cutoff must not drop nodes"
    )


def test_a_relationship_the_text_ends_stops_at_its_to_chapter(db, seed_novel):
    """The other half of the contract: to_chapter is honoured when it means
    what it says, so a genuinely severed relationship does leave the graph."""
    seeded = seed_novel(db)
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO entities (novel_id, entity_type, name) VALUES (%s,'character','Rurik') RETURNING id",
            (seeded["novel_id"],),
        )
        rurik_eid = str(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO characters (novel_id, entity_id, name, first_appearance_chapter)"
            " VALUES (%s,%s,'Rurik',1)",
            (seeded["novel_id"], rurik_eid),
        )
        cur.execute(
            "INSERT INTO relationships (entity_a_id, entity_b_id, rel_type, from_chapter, to_chapter)"
            " VALUES (%s,%s,'sworn_to',1,2)",
            (seeded["char_a_eid"], rurik_eid),
        )

    assert any(
        e["label"] == "sworn_to"
        for e in graphs_reads.relationship_graph(db, seeded["novel_id"], up_to_chapter=2)["edges"]
    )
    assert not any(
        e["label"] == "sworn_to"
        for e in graphs_reads.relationship_graph(db, seeded["novel_id"], up_to_chapter=3)["edges"]
    )
