"""Continuum MCP server: cutoff-aware novel-data tools for writing agents.

Tool definitions only — zero SQL. Lookups take `writing_chapter` and show the
world as of the chapter BEFORE it: while writing chapter N you see <= N-1.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from api import queries as api_queries
from mcp_server import queries
from reads import chapters as chapters_reads
from reads import characters as characters_reads
from reads import graphs as graphs_reads
from reads import novels as novels_reads
from reads import timeline as timeline_reads
from reads import db as reads_db

mcp = FastMCP("continuum")


def _call(fn: Callable[[], Any]) -> Any:
    """Never raise into the transport; agents self-correct from error dicts."""
    try:
        return fn()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def list_novels() -> Any:
    """List all novels: id, title, author, and highest chapter number."""
    return _call(lambda: novels_reads.list_novels(reads_db.get_db()))


@mcp.tool()
def list_chapters(novel_id: str) -> Any:
    """List every chapter of a novel (number, title, summary, critique summary)
    for orientation."""
    return _call(
        lambda: chapters_reads.list_chapters(reads_db.get_db(), UUID(novel_id), None)
    )


@mcp.tool()
def search_story(novel_id: str, query: str, writing_chapter: int, k: int = 8) -> Any:
    """Semantic + keyword search over prose and extracted facts from chapters
    before writing_chapter. Use for 'has X happened yet?' style questions."""
    return _call(lambda: queries.search_story(novel_id, query, writing_chapter, k=k))


@mcp.tool()
def get_character(novel_id: str, name: str, writing_chapter: int) -> Any:
    """A character's identity, latest state, state history, relationships, and
    events as of the chapter before writing_chapter."""
    return _call(
        lambda: characters_reads.get_character_page(
            reads_db.get_db(), UUID(novel_id), name, up_to_chapter=writing_chapter - 1
        )
    )


@mcp.tool()
def character_knowledge(
    novel_id: str, writing_chapter: int, character_id: str | None = None
) -> Any:
    """Who knows what (theory of mind), plus location and possession history,
    before writing_chapter. Optionally restrict 'knows' to one character id."""

    def run() -> Any:
        cap = writing_chapter - 1
        nid = UUID(novel_id)
        cid = UUID(character_id) if character_id else None
        return {
            "knows": api_queries.list_knows_edges(nid, cap, cid),
            "locations_history": api_queries.list_location_edges(nid, cap, False),
            "possessions": api_queries.list_possession_edges(nid, cap, False),
        }

    return _call(run)


@mcp.tool()
def relationships(novel_id: str, writing_chapter: int) -> Any:
    """Character relationship graph (nodes + typed edges) established before
    writing_chapter."""
    return _call(
        lambda: graphs_reads.relationship_graph(
            reads_db.get_db(), UUID(novel_id), writing_chapter - 1
        )
    )


@mcp.tool()
def open_threads(novel_id: str, writing_chapter: int) -> Any:
    """Plot threads opened before writing_chapter and not yet closed at that
    point, each with its capped event history. Thread status/closed_chapter fields
    reflect the full novel; point-in-time filtering is accurate when writing the
    next unwritten chapter."""
    return _call(
        lambda: queries.list_open_threads(novel_id, up_to_chapter=writing_chapter - 1)
    )


@mcp.tool()
def unresolved_commitments(novel_id: str, writing_chapter: int) -> Any:
    """Foreshadowing planted before writing_chapter that still awaits payoff. Uses
    the novel-wide 'pending' status, so results are point-in-time accurate only when
    writing the next unwritten chapter (a commitment paid off in a later existing
    chapter won't appear)."""
    return _call(
        lambda: api_queries.list_commitments(
            UUID(novel_id), writing_chapter - 1, "pending"
        )
    )


@mcp.tool()
def timeline_events(novel_id: str, writing_chapter: int) -> Any:
    """Story events grouped by chapter, from chapters before writing_chapter.
    Cutoff-aware: safe against spoilers."""
    return _call(
        lambda: timeline_reads.list_timeline(
            reads_db.get_db(), UUID(novel_id), writing_chapter - 1
        )
    )


@mcp.tool()
def canon_facts(novel_id: str, locked_only: bool = False) -> Any:
    """Established world facts. locked_only=True limits to facts whose
    contradiction is a hard continuity failure."""
    return _call(lambda: api_queries.list_canon_facts(UUID(novel_id), locked_only))


@mcp.tool()
def scene_list(novel_id: str, writing_chapter: int, chapter: int | None = None) -> Any:
    """Scene segmentation (POV, location, present characters, summary) for
    chapters before writing_chapter; optionally one specific chapter."""
    return _call(
        lambda: chapters_reads.list_scenes(
            reads_db.get_db(), UUID(novel_id), writing_chapter - 1, chapter
        )
    )


@mcp.tool()
def check_continuity(novel_id: str, chapter_number: int, draft_text: str) -> Any:
    """Run the continuity critic on a draft WITHOUT saving it. Returns
    passed/fails/warns with quotes and suggested fixes. Always run this
    before save_chapter."""
    return _call(lambda: queries.check_continuity(novel_id, chapter_number, draft_text))


@mcp.tool()
def save_chapter(
    novel_id: str, chapter_number: int, text: str, title: str | None = None
) -> Any:
    """Ingest a finished draft into the novel as a generated chapter. Runs the
    full extraction pipeline. Refuses to overwrite an existing chapter."""
    return _call(lambda: queries.save_chapter(novel_id, chapter_number, text, title))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
