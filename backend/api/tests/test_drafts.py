"""HTTP surface for the draft review queue."""

from __future__ import annotations

import json

import pytest

from pipeline import drafts as drafts_mod
from testing.drafts import _force_mock_llm  # noqa: F401  (autouse fixture)


def _park(db, novel_id: str, number: int = 90) -> str:
    findings = {"fails": [{"check": "knowledge_state", "severity": "FAIL",
                           "message": "nope", "quote": "q",
                           "suggested_fix": None, "context": {}}], "warns": []}
    return str(
        db.fetchval(
            """
            INSERT INTO draft_submissions
                (novel_id, chapter_number, title, raw_text, status, findings)
            VALUES (%s, %s, 'Ch 90', 'Elara walked in.', 'pending', %s::jsonb)
            RETURNING id
            """,
            (novel_id, number, json.dumps(findings)),
            commit=True,
        )
    )


def test_list_drafts_returns_pending(client, real_db, seed_novel_real):
    novel_id = seed_novel_real(real_db)["novel_id"]
    submission_id = _park(real_db, novel_id)

    res = client.get(f"/api/novels/{novel_id}/drafts")

    assert res.status_code == 200
    body = res.json()
    assert [r["id"] for r in body] == [submission_id]
    assert body[0]["fail_count"] == 1


def test_pending_count_endpoint(client, real_db, seed_novel_real):
    novel_id = seed_novel_real(real_db)["novel_id"]
    _park(real_db, novel_id)

    res = client.get(f"/api/novels/{novel_id}/drafts/pending-count")

    assert res.status_code == 200
    assert res.json()["pending"] == 1


def test_get_draft_returns_text_and_findings(client, real_db, seed_novel_real):
    novel_id = seed_novel_real(real_db)["novel_id"]
    submission_id = _park(real_db, novel_id)

    res = client.get(f"/api/drafts/{submission_id}")

    assert res.status_code == 200
    body = res.json()
    assert body["raw_text"] == "Elara walked in."
    assert body["findings"]["fails"][0]["check"] == "knowledge_state"


def test_get_missing_draft_404s(client):
    res = client.get("/api/drafts/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


def test_reject_endpoint_resolves_the_draft(client, real_db, seed_novel_real):
    novel_id = seed_novel_real(real_db)["novel_id"]
    submission_id = _park(real_db, novel_id)

    res = client.post(f"/api/drafts/{submission_id}/reject", json={"note": "off voice"})

    assert res.status_code == 200
    assert res.json()["rejected"] is True
    assert client.get(f"/api/novels/{novel_id}/drafts").json() == []


def test_rejecting_twice_returns_409(client, real_db, seed_novel_real):
    novel_id = seed_novel_real(real_db)["novel_id"]
    submission_id = _park(real_db, novel_id)
    client.post(f"/api/drafts/{submission_id}/reject", json={"note": "no"})

    res = client.post(f"/api/drafts/{submission_id}/reject", json={"note": "again"})

    assert res.status_code == 409


def test_accept_endpoint_ingests_the_draft(client, real_db, seed_novel_real):
    novel_id = seed_novel_real(real_db)["novel_id"]
    submission_id = _park(real_db, novel_id)

    res = client.post(
        f"/api/drafts/{submission_id}/accept", json={"note": "deliberate retcon"}
    )

    assert res.status_code == 200
    body = res.json()
    assert body["accepted"] is True
    assert body["flags_written"] == 1
