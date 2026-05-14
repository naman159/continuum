from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import app
from api.jobs import JobRecord

client = TestClient(app)


def _novel_id():
    return str(uuid4())


def _fake_novel(novel_id: str) -> dict:
    return {
        "id": novel_id,
        "title": "Test",
        "author": None,
        "language": "en",
        "created_at": "2026-01-01T00:00:00",
        "max_chapter": 1,
    }


def test_submit_job_returns_202_with_job_id():
    novel_id = _novel_id()
    with patch("api.routes.process.queries.get_novel", return_value=_fake_novel(novel_id)), \
         patch("api.routes.process.submit_job", return_value="job-abc") as mock_submit:
        response = client.post(
            f"/api/novels/{novel_id}/chapters/process",
            json={"number": 2, "text": "Chapter text here."},
        )
    assert response.status_code == 202
    assert response.json()["job_id"] == "job-abc"
    mock_submit.assert_called_once_with(
        novel_id=novel_id,
        chapter_number=2,
        text="Chapter text here.",
    )


def test_submit_job_404_when_novel_missing():
    with patch("api.routes.process.queries.get_novel", return_value=None):
        response = client.post(
            f"/api/novels/{_novel_id()}/chapters/process",
            json={"number": 1, "text": "Some text."},
        )
    assert response.status_code == 404


def test_submit_job_422_when_text_blank():
    novel_id = _novel_id()
    with patch("api.routes.process.queries.get_novel", return_value=_fake_novel(novel_id)):
        response = client.post(
            f"/api/novels/{novel_id}/chapters/process",
            json={"number": 1, "text": "   "},
        )
    assert response.status_code == 422


def test_get_job_returns_status():
    record = JobRecord(
        job_id="abc",
        status="running",
        current_pass="events",
        passes_done=4,
        total_passes=7,
    )
    with patch("api.routes.process.get_job", return_value=record):
        response = client.get("/api/jobs/abc")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["current_pass"] == "events"
    assert body["passes_done"] == 4
    assert body["total_passes"] == 7


def test_get_job_404_for_unknown():
    with patch("api.routes.process.get_job", return_value=None):
        response = client.get("/api/jobs/unknown")
    assert response.status_code == 404
