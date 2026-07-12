from __future__ import annotations

from api import admin


def test_list_novels_returns_max_chapter(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get("/api/novels")
    assert response.status_code == 200
    body = response.json()
    mine = next(r for r in body if r["id"] == seeded["novel_id"])
    assert mine["max_chapter"] == 3


def test_get_novel(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.get(f"/api/novels/{seeded['novel_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["max_chapter"] == 3
    assert body["id"] == seeded["novel_id"]


def test_get_novel_404(client):
    response = client.get("/api/novels/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_create_novel(client):
    response = client.post("/api/novels", json={"title": "New Novel", "author": "Me", "language": "en"})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "New Novel"
    assert body["author"] == "Me"
    assert body["language"] == "en"
    assert body["max_chapter"] == 0
    assert "id" in body
    assert "created_at" in body
    admin.delete_novel(body["id"])


def test_create_novel_optional_fields_omitted(client):
    response = client.post("/api/novels", json={"title": "Minimal"})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Minimal"
    assert body["author"] is None
    assert body["language"] is None
    admin.delete_novel(body["id"])


def test_create_novel_blank_title(client):
    response = client.post("/api/novels", json={"title": "   "})
    assert response.status_code == 422


def test_create_novel_missing_title(client):
    response = client.post("/api/novels", json={})
    assert response.status_code == 422


def test_delete_novel(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)
    response = client.delete(f"/api/novels/{seeded['novel_id']}")
    assert response.status_code == 204
    assert client.get(f"/api/novels/{seeded['novel_id']}").status_code == 404


def test_delete_novel_404(client):
    response = client.delete("/api/novels/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_delete_novel_real():
    """Regression test for the real (Postgres) code path."""

    class RealDBStub:
        def __init__(self, found: bool) -> None:
            self.found = found
            self.calls: list[tuple] = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            self.calls.append((query, params, commit))
            return {"id": params[0]} if self.found else None

    found_db = RealDBStub(found=True)
    assert admin._delete_novel_real(found_db, "some-id") is True
    assert found_db.calls[0][2] is True  # commit=True

    missing_db = RealDBStub(found=False)
    assert admin._delete_novel_real(missing_db, "some-id") is False


def test_create_novel_real_persists_custom_entity_types():
    """Regression test for the real (Postgres) code path: a stub matching
    DBClient's actual method signatures ensures a call like
    `db.execute(..., commit=True)` fails loudly instead of being silently
    skipped by mocks."""

    class RealDBStub:
        def __init__(self) -> None:
            self.executed: list[tuple] = []

        def fetchone(self, query, params=None, *, dict_rows=False, commit=False):
            return {
                "id": params[0],
                "title": params[1],
                "author": params[2],
                "language": params[3],
                "created_at": "2026-01-01T00:00:00Z",
            }

        def execute(self, query, params=None) -> None:
            self.executed.append(params)

    db = RealDBStub()
    result = admin._create_novel_real(
        db, "Real Novel", None, None, [{"name": "deity", "description": "test"}]
    )

    assert result["title"] == "Real Novel"
    assert db.executed == [(result["id"], "deity", "test")]
