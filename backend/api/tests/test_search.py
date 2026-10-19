from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _R:
    item_id: str = "record-1"
    metadata: dict = field(default_factory=dict)
    kind: str = "chapter"
    chapter_number: int = 1
    score: float = 0.9
    snippet: str = "Aelric took the dagger"


class FakeRetriever:
    def __init__(self):
        self.last_query = None

    def retrieve(self, query):
        self.last_query = query
        return [_R()]


def test_search_endpoint_returns_results(seed_novel_real, real_db, client, monkeypatch):
    seeded = seed_novel_real(real_db)
    fake = FakeRetriever()
    monkeypatch.setattr("reads.search._build_retriever", lambda db: fake)

    response = client.get(f"/api/novels/{seeded['novel_id']}/search?q=dagger&cap=2")

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [
        {
            "kind": "chapter",
            "item_id": "record-1",
            "record": None,
            "chapter_number": 1,
            "score": 0.9,
            "snippet": "Aelric took the dagger",
        }
    ]
    assert fake.last_query.max_chapter == 2
    assert fake.last_query.k == 8


def test_search_requires_nonempty_query(client):
    from uuid import uuid4

    response = client.get(f"/api/novels/{uuid4()}/search?q=")
    assert response.status_code == 422
