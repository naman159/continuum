from __future__ import annotations

import asyncio

from pipeline.db.client import DBClient
from reads.tests import seeding

from mcp_server import server


EXPECTED_TOOLS = {
    "list_novels", "list_chapters", "search_story", "get_character",
    "character_knowledge", "relationships", "open_threads",
    "unresolved_commitments", "timeline_events", "canon_facts",
    "scene_list", "check_continuity", "save_chapter",
}


def test_server_exposes_exactly_the_expected_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_every_tool_has_a_docstring_description():
    tools = asyncio.run(server.mcp.list_tools())
    for tool in tools:
        assert tool.description, f"tool {tool.name} has no description"


def test_tool_errors_come_back_as_error_dicts(monkeypatch):
    def boom(db, novel_id, name, up_to_chapter=None):
        raise ValueError("Character not found: Mara; closest names: Marla")

    monkeypatch.setattr(server.characters_reads, "get_character_page", boom)
    out = server.get_character("00000000-0000-0000-0000-000000000000", "Mara", 5)
    assert out["error"].endswith("closest names: Marla")


def test_writing_chapter_converts_to_inclusive_cap(monkeypatch):
    seen = {}

    def fake_page(db, novel_id, name, up_to_chapter=None):
        seen["cap"] = up_to_chapter
        return {"identity": {"name": name}}

    monkeypatch.setattr(server.characters_reads, "get_character_page", fake_page)
    server.get_character("00000000-0000-0000-0000-000000000000", "Jake", 12)
    assert seen["cap"] == 11


def test_bad_uuid_is_an_error_dict_not_an_exception():
    out = server.canon_facts("not-a-uuid", 3)
    assert "error" in out


def test_list_chapters_caps_below_the_chapter_being_written(monkeypatch):
    """Regression guard: list_chapters took no writing_chapter and passed
    up_to_chapter=None, so it returned summary_long for every unwritten
    chapter — handing the drafting agent the rest of the book."""
    seen = {}

    def fake_list(db, novel_id, up_to_chapter=None):
        seen["cap"] = up_to_chapter
        return []

    monkeypatch.setattr(server.chapters_reads, "list_chapters", fake_list)
    server.list_chapters("00000000-0000-0000-0000-000000000000", 12)
    assert seen["cap"] == 11


def test_canon_facts_caps_below_the_chapter_being_written(monkeypatch):
    """writing_chapter used to default to None, which resolves to MAX(number)
    — leaking facts sourced from chapters the agent has not written."""
    seen = {}

    def fake_list(db, novel_id, up_to_chapter, locked_only):
        seen["cap"] = up_to_chapter
        return []

    monkeypatch.setattr(server.knowledge_reads, "list_canon_facts", fake_list)
    server.canon_facts("00000000-0000-0000-0000-000000000000", 5)
    assert seen["cap"] == 4


def test_timeline_events_requires_writing_chapter_and_converts_to_cap(monkeypatch):
    """Regression guard: timeline_events used to query a nonexistent `timeline`
    table (always an error dict) and took no writing_chapter at all. It must now
    call reads.timeline.list_timeline with an inclusive up_to_chapter cap."""
    seen = {}

    def fake_list_timeline(db, novel_id, up_to_chapter):
        seen["novel_id"] = novel_id
        seen["up_to_chapter"] = up_to_chapter
        return [{"chapter_number": 1, "description": "ok"}]

    monkeypatch.setattr(server.timeline_reads, "list_timeline", fake_list_timeline)
    out = server.timeline_events("00000000-0000-0000-0000-000000000000", 5)
    assert "error" not in out
    assert seen["up_to_chapter"] == 4
    assert out == [{"chapter_number": 1, "description": "ok"}]


def test_search_story_converts_writing_chapter_to_cap(monkeypatch):
    """Regression guard: search_story must be wired to reads.search.search
    (shared with the /api/search route), not the deleted queries.search_story."""
    seen = {}

    def fake_search(db, novel_id, query_text, up_to_chapter, k=8):
        seen["query_text"] = query_text
        seen["up_to_chapter"] = up_to_chapter
        seen["k"] = k
        return {"results": []}

    monkeypatch.setattr(server.search_reads, "search", fake_search)
    out = server.search_story("00000000-0000-0000-0000-000000000000", "the letter", 12, k=3)
    assert "error" not in out
    assert seen["query_text"] == "the letter"
    assert seen["up_to_chapter"] == 11
    assert seen["k"] == 3


def test_relationships_tool_converts_writing_chapter_to_cap(monkeypatch):
    """Regression guard: relationships must be wired to the merged
    reads.graphs.relationship_graph, not left calling a removed queries function."""
    seen = {}

    def fake_relationship_graph(db, novel_id, up_to_chapter):
        seen["up_to_chapter"] = up_to_chapter
        return {"nodes": [], "edges": [], "up_to_chapter": up_to_chapter}

    monkeypatch.setattr(server.graphs_reads, "relationship_graph", fake_relationship_graph)
    out = server.relationships("00000000-0000-0000-0000-000000000000", 5)
    assert "error" not in out
    assert seen["up_to_chapter"] == 4
    assert out["nodes"] == []


# ---------------------------------------------------------------------------
# Spoiler masking: unresolved_commitments / open_threads (real DB, seed factory)
# ---------------------------------------------------------------------------


def test_unresolved_commitments_masks_future_payoff_before_satisfied():
    """Commitment foreshadowed ch1, paid off ch3 (seed factory). At
    writing_chapter=3 (cutoff=2) it's still pending -> payoff_text/
    payoff_chapter must be masked to None and status forced to 'pending' so
    the future payoff doesn't leak. At writing_chapter=4 (cutoff=3) it's
    satisfied and drops out of the pending-only tool entirely."""
    db = DBClient()
    seeded = None
    try:
        seeded = seeding.seed_novel(db)
        novel_id = seeded["novel_id"]

        rows = server.unresolved_commitments(novel_id, 3)
        assert isinstance(rows, list)
        matches = [r for r in rows if str(r["id"]) == seeded["commitment_id"]]
        assert len(matches) == 1
        row = matches[0]
        assert row["status_at_cutoff"] == "pending"
        assert row["payoff_text"] is None
        assert row["payoff_chapter"] is None
        assert row["status"] == "pending"

        rows_after = server.unresolved_commitments(novel_id, 4)
        assert all(str(r["id"]) != seeded["commitment_id"] for r in rows_after)
    finally:
        if seeded is not None:
            seeding.cleanup(db, seeded["novel_id"])
        db.close()


def test_open_threads_masks_future_closure_before_closed():
    """Plot thread opened ch1, closed ch3 (seed factory). At
    writing_chapter=3 (cutoff=2) it still reads as open -> closed_chapter/
    status must be masked so the future closure doesn't leak. At
    writing_chapter=4 (cutoff=3) it's closed and drops out of open_threads."""
    db = DBClient()
    seeded = None
    try:
        seeded = seeding.seed_novel(db)
        novel_id = seeded["novel_id"]

        rows = server.open_threads(novel_id, 3)
        assert isinstance(rows, list)
        matches = [r for r in rows if str(r["id"]) == seeded["thread_id"]]
        assert len(matches) == 1
        row = matches[0]
        assert row["status_at_cutoff"] == "progressing"
        assert row["closed_chapter"] is None
        assert row["status"] == "progressing"

        rows_after = server.open_threads(novel_id, 4)
        assert all(str(r["id"]) != seeded["thread_id"] for r in rows_after)
    finally:
        if seeded is not None:
            seeding.cleanup(db, seeded["novel_id"])
        db.close()
