"""reads.common: cutoff resolution against real Postgres."""

from __future__ import annotations

from reads.common import max_chapter_for, resolve_cutoff


def test_resolve_cutoff_none_means_latest(db, seed_novel):
    seeded = seed_novel(db)
    assert max_chapter_for(db, seeded["novel_id"]) == 3
    assert resolve_cutoff(db, seeded["novel_id"], None) == 3
    assert resolve_cutoff(db, seeded["novel_id"], 1) == 1


def test_merge_story_edges_collapses_pairs():
    from reads.common import merge_story_edges
    raw = [
        {"from": "a", "to": "b", "edge_kind": "event", "description": "fought"},
        {"from": "b", "to": "a", "edge_kind": "dynamic", "description": "rivals"},
    ]
    merged = merge_story_edges(raw)
    assert len(merged) == 1
    assert merged[0]["edge_kind"] == "dynamic"  # dynamic outranks event
