"""Continuum MCP server: cutoff-aware novel-data tools for writing agents.

Tool definitions only — zero SQL. Lookups take `writing_chapter` and show the
world as of the chapter BEFORE it: while writing chapter N you see <= N-1.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from mcp_server import queries
from reads import chapters as chapters_reads
from reads import characters as characters_reads
from reads import commitments as commitments_reads
from reads import graphs as graphs_reads
from reads import knowledge as knowledge_reads
from reads import novels as novels_reads
from reads import search as search_reads
from reads import threads as threads_reads
from reads import timeline as timeline_reads
from reads import db as reads_db
from reads import world as world_reads

mcp = FastMCP("continuum", instructions="""
Use structured tools first for story facts. Interpret the request, discover entity
IDs with list_story_entities, then call the relevant tool: possessions for ownership,
entity_locations for whereabouts, known_facts for who knows what, relationships
for character connections, get_world_entity for world details, canon_facts for rules,
and timeline_events, open_threads or unresolved_commitments for plot questions.
Combine tool calls for questions involving multiple kinds of facts. search_story is
for discovering relevant scenes/events when structured lookups do not suffice.
All lookups use writing_chapter N to read through N-1. Returned absence means no
recorded evidence, not proof that something is false. Cite returned record IDs and
chapter anchors. Entity IDs and typed IDs differ: use the IDs documented by each tool.
""")


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
def list_chapters(novel_id: str, writing_chapter: int) -> Any:
    """List the chapters written *before* writing_chapter (number, title,
    summary, critique summary) for orientation. Chapters from writing_chapter
    onward are withheld: returning them would hand the drafting agent the plot
    of the book it has not written yet."""
    return _call(
        lambda: chapters_reads.list_chapters(
            reads_db.get_db(), UUID(novel_id), writing_chapter - 1
        )
    )


@mcp.tool()
def search_story(novel_id: str, query: str, writing_chapter: int, k: int = 8) -> Any:
    """Discover relevant chapter/scene summaries, events, character knowledge,
    canon facts and commitments before
    writing_chapter using semantic + keyword search. Use for finding narrative
    evidence or unknown events. For ownership, whereabouts, knowledge, rules,
    and relationships, prefer the corresponding structured tools first.
    Results include item_id and, for structured matches, the actual record with
    source/chapter references. Search is not a complete inventory of facts."""
    return _call(
        lambda: search_reads.search(
            reads_db.get_db(), UUID(novel_id), query, writing_chapter - 1, k=k
        )
    )


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
    """Broad legacy overview; prefer known_facts, possessions and entity_locations
    for focused questions. Who knows what, plus novel-wide location and possession history,
    before writing_chapter. Optionally restrict 'knows' to one character id."""

    def run() -> Any:
        cap = writing_chapter - 1
        nid = UUID(novel_id)
        cid = UUID(character_id) if character_id else None
        db = reads_db.get_db()
        return {
            "knows": knowledge_reads.list_knows_edges(db, nid, cap, cid),
            "locations_history": knowledge_reads.list_location_edges(db, nid, cap, False),
            "possessions": knowledge_reads.list_possession_edges(db, nid, cap, False),
        }

    return _call(run)


@mcp.tool()
def list_story_entities(novel_id: str, writing_chapter: int, entity_type: str = "character") -> Any:
    """Discover names, aliases and IDs before writing_chapter. entity_type is
    character, location, object, faction, or a custom type. Returned id is the
    TYPED ID for built-in types, the universal entity ID for custom types.
    Use these IDs in possessions, known_facts and get_world_entity. For location
    lookups by subject, use entity_locations' entity_name filter instead."""
    def run() -> Any:
        db, nid, cap = reads_db.get_db(), UUID(novel_id), writing_chapter - 1
        readers = {"character": characters_reads.list_characters,
                   "location": world_reads.list_locations, "object": world_reads.list_objects,
                   "faction": world_reads.list_factions}
        if entity_type in readers:
            return readers[entity_type](db, nid, cap)
        return world_reads.list_custom_entities(db, nid, entity_type, cap)
    return _call(run)


@mcp.tool()
def get_world_entity(novel_id: str, writing_chapter: int, entity_type: str, entity_id: str) -> Any:
    """Read an object, location, faction or custom entity's details and linked
    events/relationships before writing_chapter. First discover its typed ID
    with list_story_entities. For characters use get_character by name."""
    def run() -> Any:
        db, nid, cap, eid = reads_db.get_db(), UUID(novel_id), writing_chapter - 1, UUID(entity_id)
        # Validate against the cutoff-aware inventory before fetching details.
        rows = list_story_entities(novel_id, writing_chapter, entity_type)
        if isinstance(rows, dict) and "error" in rows:
            return rows
        if not any(str(row["id"]) == str(eid) for row in rows):
            raise ValueError("Entity not found at this chapter")
        readers = {"location": world_reads.get_location_detail,
                   "object": world_reads.get_object_detail, "faction": world_reads.get_faction_detail}
        if entity_type == "character":
            raise ValueError("Use get_character for characters")
        return readers.get(entity_type, world_reads.get_custom_entity_detail)(db, nid, eid, cap)
    return _call(run)


@mcp.tool()
def possessions(novel_id: str, writing_chapter: int, character_id: str | None = None,
                object_id: str | None = None, active_only: bool = True) -> Any:
    """Who owns/carries an object, or what a character has, before writing_chapter.
    Filter by typed character_id and/or object_id from list_story_entities.
    active_only=False returns ownership history. Includes record IDs, certainty
    and chapter intervals; an empty result means ownership is unrecorded."""
    return _call(lambda: knowledge_reads.list_possession_edges(
        reads_db.get_db(), UUID(novel_id), writing_chapter - 1, active_only,
        UUID(character_id) if character_id else None, UUID(object_id) if object_id else None))


@mcp.tool()
def entity_locations(novel_id: str, writing_chapter: int, entity_name: str | None = None,
                     location_id: str | None = None, active_only: bool = True) -> Any:
    """Where an entity is, or which entities are at a location, before writing_chapter.
    entity_name matches the canonical name exactly, ignoring case. location_id is
    the typed location ID from list_story_entities. active_only=False returns
    movement history. Includes universal entity IDs and chapter intervals."""
    def run() -> Any:
        return knowledge_reads.list_location_edges(
            reads_db.get_db(), UUID(novel_id), writing_chapter - 1, active_only,
            location_id=UUID(location_id) if location_id else None, entity_name=entity_name)
    return _call(run)


@mcp.tool()
def known_facts(novel_id: str, writing_chapter: int, character_id: str | None = None,
                fact_contains: str | None = None) -> Any:
    """What a character knows, or who knows a given fact, before writing_chapter.
    character_id is the typed ID from list_story_entities. fact_contains is an
    optional literal case-insensitive substring, not semantic search; omit it
    to inspect all recorded knowledge when wording is uncertain. Returns learning
    chapter, source event, certainty and sharing evidence. Missing records do not
    establish ignorance. Use this instead of searching chapter summaries."""
    return _call(lambda: knowledge_reads.list_knows_edges(
        reads_db.get_db(), UUID(novel_id), writing_chapter - 1,
        UUID(character_id) if character_id else None, fact_contains))


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
    """Plot threads opened before writing_chapter and not yet closed as of that
    point, each with its capped event history. status_at_cutoff reflects the
    world as of writing_chapter - 1, so this is point-in-time accurate for any
    chapter, not just the next unwritten one. closed_chapter and status are
    future-anchored (the novel-wide truth): for any thread not yet closed at
    the cutoff, both are masked (closed_chapter=None, status=status_at_cutoff)
    so a thread closed in a later existing chapter doesn't leak its closure."""

    def run() -> Any:
        rows = threads_reads.list_threads(
            reads_db.get_db(), UUID(novel_id), writing_chapter - 1, status="all"
        )
        open_rows = [r for r in rows if r["status_at_cutoff"] != "closed"]
        for r in open_rows:
            if r["status_at_cutoff"] != "closed":
                r["closed_chapter"] = None
                r["status"] = r["status_at_cutoff"]
        return open_rows

    return _call(run)


@mcp.tool()
def unresolved_commitments(novel_id: str, writing_chapter: int) -> Any:
    """Foreshadowing planted before writing_chapter that still awaits payoff as of
    that point. status_at_cutoff reflects the world as of writing_chapter - 1, so
    this is point-in-time accurate for any chapter, not just the next unwritten one.
    payoff_text, payoff_chapter, and status are future-anchored (the novel-wide
    truth): for any commitment still pending at the cutoff, all three are masked
    (payoff_text=None, payoff_chapter=None, status="pending") so a commitment paid
    off in a later existing chapter doesn't leak its payoff."""

    def run() -> Any:
        rows = commitments_reads.list_commitments(
            reads_db.get_db(), UUID(novel_id), writing_chapter - 1, status="pending"
        )
        for r in rows:
            if r["status_at_cutoff"] == "pending":
                r["payoff_text"] = None
                r["payoff_chapter"] = None
                r["status"] = "pending"
        return rows

    return _call(run)


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
def canon_facts(
    novel_id: str, writing_chapter: int, locked_only: bool = False
) -> Any:
    """Established world facts as of the chapter before writing_chapter.
    locked_only=True limits to facts whose contradiction is a hard continuity
    failure. Facts sourced from writing_chapter or later are excluded (cutoff =
    writing_chapter - 1), matching the other cutoff-aware tools."""

    def run() -> Any:
        return knowledge_reads.list_canon_facts(
            reads_db.get_db(), UUID(novel_id), writing_chapter - 1, locked_only
        )

    return _call(run)


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
    passed/fails/warns with quotes and suggested fixes, plus a `status`.
    Use this while revising: save_chapter runs the identical critique and will
    refuse a draft that fails it. `status` is "ok" when the verdict is real;
    "unavailable" or "error" mean the critique did not run, and `passed: false`
    there is an outage, not a judgement on the draft."""
    return _call(lambda: queries.check_continuity(novel_id, chapter_number, draft_text))


@mcp.tool()
def save_chapter(
    novel_id: str, chapter_number: int, text: str, title: str | None = None
) -> Any:
    """Ingest a finished draft into the novel as a generated chapter.

    The continuity critique runs BEFORE extraction, and a draft with any FAIL
    finding is refused and parked for human review — this tool is the one
    caller that blocks on a FAIL rather than recording it and ingesting.
    A refusal returns {"ingested": false, "status", "submission_id", "reason",
    "fails", "warns"}. Two refusals look alike but need different responses:
      - status "pending_review" (reason "fail" or "error"): `submission_id` is
        set — the draft is parked and waiting on a human. Revise against
        `fails` and resubmit; a resubmission supersedes the parked draft.
      - status "refused" (reason "unavailable"): `submission_id` is null —
        nothing was parked because there was no verdict to record. This is an
        outage (the critique is switched off, or is running without a real
        model), not a verdict on the draft. Hold the text and retry
        `save_chapter` later rather than revising against `fails` (which will
        be empty) or resubmitting into the queue.
    Warnings do not block. Refuses to overwrite an existing chapter."""
    return _call(lambda: queries.save_chapter(novel_id, chapter_number, text, title))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
