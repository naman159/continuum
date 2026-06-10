from __future__ import annotations

from api.tests.conftest import make_novel


def test_generate_endpoint_submits_job(fake_db_factory, client, monkeypatch):
    novel = make_novel()
    fake_db_factory(novels=[novel], chapters=[])
    captured: dict = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return "gen-job-1"

    monkeypatch.setattr("api.routes.process.submit_generation_job", fake_submit)
    response = client.post(
        f"/api/novels/{novel['id']}/chapters/generate",
        json={"number": 4, "ingest": True},
    )
    assert response.status_code == 202
    assert response.json()["job_id"] == "gen-job-1"
    assert captured["chapter_number"] == 4
    assert captured["ingest"] is True


def test_generate_endpoint_404_for_unknown_novel(fake_db_factory, client):
    fake_db_factory(novels=[], chapters=[])
    response = client.post(
        "/api/novels/00000000-0000-0000-0000-000000000001/chapters/generate",
        json={"number": 1},
    )
    assert response.status_code == 404
