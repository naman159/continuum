from __future__ import annotations

from unittest.mock import patch

from api.tests.conftest import make_novel


def test_generate_endpoint_submits_job(client, monkeypatch):
    novel = make_novel()
    captured: dict = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return "gen-job-1"

    monkeypatch.setattr("api.routes.process.submit_generation_job", fake_submit)
    with patch("api.routes.process.novels_reads.get_novel", return_value=novel), \
         patch("api.routes.process.chapters_reads.list_chapters", return_value=[]):
        response = client.post(
            f"/api/novels/{novel['id']}/chapters/generate",
            json={"number": 4, "ingest": True},
        )
    assert response.status_code == 202
    assert response.json()["job_id"] == "gen-job-1"
    assert captured["chapter_number"] == 4
    assert captured["ingest"] is True


def test_generate_endpoint_404_for_unknown_novel(client):
    with patch("api.routes.process.novels_reads.get_novel", return_value=None):
        response = client.post(
            "/api/novels/00000000-0000-0000-0000-000000000001/chapters/generate",
            json={"number": 1},
        )
    assert response.status_code == 404


def test_generate_endpoint_409_when_chapter_exists(client):
    novel = make_novel()
    with patch("api.routes.process.novels_reads.get_novel", return_value=novel), \
         patch(
             "api.routes.process.chapters_reads.list_chapters",
             return_value=[{"number": 4}],
         ):
        response = client.post(
            f"/api/novels/{novel['id']}/chapters/generate",
            json={"number": 4, "ingest": True},
        )
    assert response.status_code == 409
