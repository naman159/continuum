from __future__ import annotations

from uuid import uuid4


def test_merge_endpoint_validates_and_calls_merge(client, monkeypatch):
    captured: dict = {}

    def fake_merge(db, *, novel_id, source_entity_id, target_entity_id):
        captured.update(novel_id=novel_id, src=source_entity_id, tgt=target_entity_id)
        return {"source_entity_id": source_entity_id, "target_entity_id": target_entity_id,
                "entity_type": "character", "absorbed_name": "Jane"}

    monkeypatch.setattr("api.routes.entity_types.merge_entities", fake_merge)
    monkeypatch.setattr("api.routes.entity_types._merge_db", lambda: object())

    novel_id, src, tgt = uuid4(), uuid4(), uuid4()
    response = client.post(
        f"/api/novels/{novel_id}/entities/merge",
        json={"source_entity_id": str(src), "target_entity_id": str(tgt)},
    )
    assert response.status_code == 200
    assert captured["src"] == str(src)


def test_merge_endpoint_maps_merge_error_to_400(client, monkeypatch):
    from pipeline.db.entity_merge import EntityMergeError

    def fake_merge(db, **kwargs):
        raise EntityMergeError("type mismatch")

    monkeypatch.setattr("api.routes.entity_types.merge_entities", fake_merge)
    monkeypatch.setattr("api.routes.entity_types._merge_db", lambda: object())

    response = client.post(
        f"/api/novels/{uuid4()}/entities/merge",
        json={"source_entity_id": str(uuid4()), "target_entity_id": str(uuid4())},
    )
    assert response.status_code == 400
