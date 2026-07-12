from __future__ import annotations


def test_relationships_graph_returns_nodes_and_edges(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/relationships")
    assert response.status_code == 200
    body = response.json()
    labels = {n["label"] for n in body["nodes"]}
    assert labels == {seeded["char_a_name"], seeded["char_b_name"]}
    assert len(body["edges"]) == 1
    edge = body["edges"][0]
    assert edge["label"] == "rival"
    assert edge["symmetric"] is True
    assert edge["chapter_number"] == 3


def test_relationships_graph_cap_hides_unintroduced_relationship(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}/relationships?cap=2")
    assert response.status_code == 200
    body = response.json()
    # char B first appears ch3 and the relationship starts ch3, so at cap=2
    # neither the (edgeless) node nor the edge should surface.
    assert body["nodes"] == []
    assert body["edges"] == []
