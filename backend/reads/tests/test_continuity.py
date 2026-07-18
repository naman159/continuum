from __future__ import annotations

from reads import continuity as continuity_reads


def test_get_chapter_critique_returns_findings(db, seed_novel):
    seeded = seed_novel(db)
    report = continuity_reads.get_chapter_critique(db, seeded["novel_id"], 2)
    assert report is not None and report["passed"] is False
    assert len(report["findings"]) == 1
    assert report["findings"][0]["severity"] == "fail"
    assert continuity_reads.get_chapter_critique(db, seeded["novel_id"], 1) is None


def test_list_critiques_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    rows = continuity_reads.list_critiques(db, seeded["novel_id"], up_to_chapter=1)
    assert rows == []  # ch2's report is beyond the cutoff
    rows = continuity_reads.list_critiques(db, seeded["novel_id"], up_to_chapter=None)
    assert [r["chapter_number"] for r in rows] == [2]


def test_unattributed_resolution_counts_resolved_only_uncapped(db, seed_novel):
    seeded = seed_novel(db)
    # Flag raised in ch1, marked resolved with no resolved_chapter_id (the
    # resolution cannot be dated) — it must not read as resolved in a capped view.
    with db.transaction() as cur:
        cur.execute(
            "SELECT id FROM chapters WHERE novel_id = %s AND number = 1",
            (seeded["novel_id"],),
        )
        ch1_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO continuity_flags (chapter_id, description, resolved)"
            " VALUES (%s, %s, true) RETURNING id",
            (ch1_id, "undated resolution"),
        )
        flag_id = str(cur.fetchone()[0])
    capped = continuity_reads.list_flags(db, seeded["novel_id"], up_to_chapter=2, resolved_filter="open")
    assert any(str(r["id"]) == flag_id for r in capped)  # still open as far as ch2 knows
    uncapped = continuity_reads.list_flags(db, seeded["novel_id"], up_to_chapter=None, resolved_filter="all")
    mine = next(r for r in uncapped if str(r["id"]) == flag_id)
    assert mine["resolved"] is True
