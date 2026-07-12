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
