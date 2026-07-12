from __future__ import annotations

from reads import commitments as commitments_reads
from reads import threads as threads_reads


def test_thread_closed_later_reads_open_at_cutoff(db, seed_novel):
    seeded = seed_novel(db)  # factory thread: opened ch1, closed_chapter=3, status='closed'
    rows = threads_reads.list_threads(db, seeded["novel_id"], up_to_chapter=2, status="all")
    mine = next(r for r in rows if str(r["id"]) == seeded["thread_id"])
    assert mine["status_at_cutoff"] == "progressing"
    assert all(e["chapter_number"] <= 2 for e in mine["events"])

    rows3 = threads_reads.list_threads(db, seeded["novel_id"], up_to_chapter=3, status="all")
    assert next(r for r in rows3 if str(r["id"]) == seeded["thread_id"])["status_at_cutoff"] == "closed"


def test_commitment_paid_off_later_is_pending_at_cutoff(db, seed_novel):
    seeded = seed_novel(db)  # factory commitment: foreshadow ch1, payoff_chapter=3, status='satisfied'
    rows = commitments_reads.list_commitments(db, seeded["novel_id"], up_to_chapter=2, status="pending")
    assert any(str(r["id"]) == seeded["commitment_id"] for r in rows)
    rows3 = commitments_reads.list_commitments(db, seeded["novel_id"], up_to_chapter=3, status="pending")
    assert not any(str(r["id"]) == seeded["commitment_id"] for r in rows3)
