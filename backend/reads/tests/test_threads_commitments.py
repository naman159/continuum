from __future__ import annotations

from reads import commitments as commitments_reads
from reads import threads as threads_reads


def test_thread_closed_later_reads_open_at_cutoff(db, seed_novel):
    seeded = seed_novel(db)  # factory thread: opened ch1, closed_chapter=3, status='closed'
    rows = threads_reads.list_threads(db, seeded["novel_id"], up_to_chapter=2, status="all")
    mine = next(r for r in rows if str(r["id"]) == seeded["thread_id"])
    assert mine["status_at_cutoff"] == "progressing"
    assert mine["status"] == "progressing"
    assert mine["closed_chapter"] is None
    assert all(e["chapter_number"] <= 2 for e in mine["events"])

    rows3 = threads_reads.list_threads(db, seeded["novel_id"], up_to_chapter=3, status="all")
    assert next(r for r in rows3 if str(r["id"]) == seeded["thread_id"])["status_at_cutoff"] == "closed"


def test_commitment_paid_off_later_is_pending_at_cutoff(db, seed_novel):
    seeded = seed_novel(db)  # factory commitment: foreshadow ch1, payoff_chapter=3, status='satisfied'
    rows = commitments_reads.list_commitments(db, seeded["novel_id"], up_to_chapter=2, status="pending")
    assert any(str(r["id"]) == seeded["commitment_id"] for r in rows)
    mine = next(r for r in rows if str(r["id"]) == seeded["commitment_id"])
    assert mine["status"] == "pending"
    assert mine["payoff_text"] is None
    assert mine["payoff_chapter"] is None
    rows3 = commitments_reads.list_commitments(db, seeded["novel_id"], up_to_chapter=3, status="pending")
    assert not any(str(r["id"]) == seeded["commitment_id"] for r in rows3)


def test_reopened_thread_with_stale_closed_chapter_reads_open(db, seed_novel):
    seeded = seed_novel(db)
    # Simulate a reopen: a later chapter set status back to 'progressing' but
    # the stale closed_chapter=3 anchor was left behind (pre-fix upsert data).
    with db.transaction() as cur:
        cur.execute(
            "UPDATE plot_threads SET status = 'progressing' WHERE id = %s",
            (seeded["thread_id"],),
        )
    for cap in (3, None):  # even at/after the stale anchor, and uncapped
        rows = threads_reads.list_threads(db, seeded["novel_id"], up_to_chapter=cap, status="all")
        mine = next(r for r in rows if str(r["id"]) == seeded["thread_id"])
        assert mine["status_at_cutoff"] == "progressing", f"cap={cap}"


def test_closed_thread_without_anchor_counts_closed_only_uncapped(db, seed_novel):
    seeded = seed_novel(db)
    with db.transaction() as cur:
        cur.execute(
            "UPDATE plot_threads SET status = 'closed', closed_chapter = NULL WHERE id = %s",
            (seeded["thread_id"],),
        )
    rows = threads_reads.list_threads(db, seeded["novel_id"], up_to_chapter=2, status="all")
    mine = next(r for r in rows if str(r["id"]) == seeded["thread_id"])
    assert mine["status_at_cutoff"] == "progressing"  # undatable closure must not leak into a capped view
    rows = threads_reads.list_threads(db, seeded["novel_id"], up_to_chapter=None, status="all")
    mine = next(r for r in rows if str(r["id"]) == seeded["thread_id"])
    assert mine["status_at_cutoff"] == "closed"  # nothing is in the future at the uncapped view


def test_commitment_broken_later_is_pending_at_earlier_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    # A commitment planted in ch1 was marked broken by a later chapter's
    # extraction; the status carries no chapter anchor.
    with db.transaction() as cur:
        cur.execute(
            "UPDATE commitments SET status = 'broken', payoff_chapter = NULL WHERE id = %s",
            (seeded["commitment_id"],),
        )
    rows = commitments_reads.list_commitments(db, seeded["novel_id"], up_to_chapter=2, status="pending")
    assert any(str(r["id"]) == seeded["commitment_id"] for r in rows)  # still live as of ch2
    rows = commitments_reads.list_commitments(db, seeded["novel_id"], up_to_chapter=None, status="all")
    mine = next(r for r in rows if str(r["id"]) == seeded["commitment_id"])
    assert mine["status_at_cutoff"] == "broken"  # uncapped view shows the real status
