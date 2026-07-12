from __future__ import annotations


def test_threads_status_at_cutoff_reflects_point_in_time(seed_novel_real, real_db, client):
    """Factory thread: opened ch1, closed_chapter=3, status='closed'."""
    seeded = seed_novel_real(real_db)

    response = client.get(f"/api/novels/{seeded['novel_id']}/threads?cap=2")
    assert response.status_code == 200
    rows = response.json()
    mine = next(r for r in rows if r["id"] == seeded["thread_id"])
    # Raw columns reflect the novel-wide truth; status_at_cutoff reflects
    # what a reader at chapter 2 would see.
    assert mine["status"] == "closed"
    assert mine["closed_chapter"] == 3
    assert mine["status_at_cutoff"] == "progressing"
    assert all(e["chapter_number"] <= 2 for e in mine["events"])

    response3 = client.get(f"/api/novels/{seeded['novel_id']}/threads?cap=3")
    rows3 = response3.json()
    mine3 = next(r for r in rows3 if r["id"] == seeded["thread_id"])
    assert mine3["status_at_cutoff"] == "closed"


def test_threads_status_filter_uses_status_at_cutoff(seed_novel_real, real_db, client):
    seeded = seed_novel_real(real_db)

    # At cap=2 the thread hasn't closed yet at cutoff: filtering for
    # status=closed excludes it, status=progressing includes it.
    closed_at_cap2 = client.get(f"/api/novels/{seeded['novel_id']}/threads?cap=2&status=closed")
    assert closed_at_cap2.status_code == 200
    assert not any(r["id"] == seeded["thread_id"] for r in closed_at_cap2.json())

    progressing_at_cap2 = client.get(
        f"/api/novels/{seeded['novel_id']}/threads?cap=2&status=progressing"
    )
    assert any(r["id"] == seeded["thread_id"] for r in progressing_at_cap2.json())

    # At cap=3 it has closed: filtering for status=closed now includes it.
    closed_at_cap3 = client.get(f"/api/novels/{seeded['novel_id']}/threads?cap=3&status=closed")
    assert any(r["id"] == seeded["thread_id"] for r in closed_at_cap3.json())
