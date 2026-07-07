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
    def boom(novel_id, name, up_to_chapter=None):
        raise ValueError("Character not found: Mara; closest names: Marla")

    monkeypatch.setattr(server.queries, "build_character_page", boom)
    out = server.get_character("novel-1", "Mara", 5)
    assert out["error"].endswith("closest names: Marla")


def test_writing_chapter_converts_to_inclusive_cap(monkeypatch):
    seen = {}

    def fake_page(novel_id, name, up_to_chapter=None):
        seen["cap"] = up_to_chapter
        return {"identity": {"name": name}}

    monkeypatch.setattr(server.queries, "build_character_page", fake_page)
    server.get_character("novel-1", "Jake", 12)
    assert seen["cap"] == 11


def test_bad_uuid_is_an_error_dict_not_an_exception():
    out = server.canon_facts("not-a-uuid")
    assert "error" in out
