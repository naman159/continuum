from __future__ import annotations

import asyncio

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
    out = server.canon_facts("not-a-uuid")
    assert "error" in out


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
