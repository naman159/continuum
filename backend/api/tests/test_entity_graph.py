from __future__ import annotations


def test_entity_graph_includes_all_entity_types_and_native_ids(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/entity-graph")
    assert response.status_code == 200
    body = response.json()

    labels = {n["label"] for n in body["nodes"]}
    assert labels == {
        seeded["char_a_name"], seeded["char_b_name"],
        seeded["loc_a_name"], seeded["loc_b_name"],
        seeded["obj_name"], seeded["faction_name"],
    }
    types = {n["entity_type"] for n in body["nodes"]}
    assert types == {"character", "location", "object", "faction"}

    char_a_node = next(n for n in body["nodes"] if n["label"] == seeded["char_a_name"])
    assert char_a_node["native_id"] == seeded["char_a_id"]


def test_entity_graph_relationship_and_dynamic_edges_coexist(seed_novel_real, real_db, client):
    """A relationship edge and a shared-dynamic story edge between the same
    pair stay separate — merge_story_edges only collapses same-kind story
    records, and relationship edges never enter that merge."""
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/entity-graph")
    assert response.status_code == 200
    edges = response.json()["edges"]
    kinds = [e["edge_kind"] for e in edges]
    assert kinds.count("relationship") == 1
    assert kinds.count("dynamic") == 1

    rel_edge = next(e for e in edges if e["edge_kind"] == "relationship")
    assert rel_edge["label"] == "rival"
    assert rel_edge["symmetric"] is True

    dyn_edge = next(e for e in edges if e["edge_kind"] == "dynamic")
    assert dyn_edge["tooltip"] == "Wary respect after the ambush."


def test_entity_graph_event_and_state_delta_story_edges(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/entity-graph")
    assert response.status_code == 200
    edges = response.json()["edges"]
    kinds = {e["edge_kind"] for e in edges}
    assert {"possession", "location", "event"} <= kinds

    event_edge = next(e for e in edges if e["edge_kind"] == "event")
    assert "Hollow Ledger" in event_edge["tooltip"]


def test_entity_graph_cap_filters_characters_and_late_edges(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/entity-graph?cap=2")
    assert response.status_code == 200
    body = response.json()

    labels = {n["label"] for n in body["nodes"]}
    assert seeded["char_b_name"] not in labels
    assert seeded["char_a_name"] in labels

    kinds = {e["edge_kind"] for e in body["edges"]}
    assert "relationship" not in kinds
    assert "dynamic" not in kinds
    assert "event" not in kinds
    assert {"possession", "location"} <= kinds
