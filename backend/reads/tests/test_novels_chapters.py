from __future__ import annotations

from reads import chapters as chapters_reads
from reads import novels as novels_reads


def test_list_novels_includes_max_chapter(db, seed_novel):
    seeded = seed_novel(db)
    rows = novels_reads.list_novels(db)
    mine = next(r for r in rows if str(r["id"]) == seeded["novel_id"])
    assert mine["max_chapter"] == 3


def test_list_chapters_respects_cutoff_and_carries_critique(db, seed_novel):
    seeded = seed_novel(db)
    rows = chapters_reads.list_chapters(db, seeded["novel_id"], up_to_chapter=2)
    assert [r["number"] for r in rows] == [1, 2]
    ch2 = next(r for r in rows if r["number"] == 2)
    assert ch2["critique"] == {"passed": False, "fails": 1, "warns": 0}
    ch1 = next(r for r in rows if r["number"] == 1)
    assert ch1["critique"] is None


def test_list_scenes_cutoff(db, seed_novel):
    seeded = seed_novel(db)
    assert chapters_reads.list_scenes(db, seeded["novel_id"], up_to_chapter=3, chapter=None)
    assert chapters_reads.list_scenes(db, seeded["novel_id"], up_to_chapter=0, chapter=None) == []
