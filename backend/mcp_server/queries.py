"""Composite, cutoff-aware lookups for the MCP writer tools.

Query functions moved from the deleted cli/ package plus MCP-specific glue.
`up_to_chapter` is always an INCLUSIVE cap; server.py converts the agent-facing
`writing_chapter` to `writing_chapter - 1` before calling in here.
"""

from __future__ import annotations

import difflib
from typing import Any

from pipeline.config import settings
from pipeline.critic.runner import ContinuityCritic
from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService
from pipeline.generation.draft_claims import build_draft_chapter, extract_draft_claims
from pipeline.pipeline import process_chapter
from pipeline.retrieval.hybrid import HybridRetriever
from pipeline.retrieval.types import RetrievalQuery


def build_character_page(novel_id: str, name: str, up_to_chapter: int | None = None) -> dict[str, Any]:
    with DBClient() as db:
        character = db.fetchone(
            """
            SELECT id, name, aliases, first_appearance_chapter, description
            FROM characters
            WHERE novel_id = %s AND lower(name) = lower(%s)
            LIMIT 1
            """,
            (novel_id, name),
            dict_rows=True,
        )
        if character is None:
            rows = db.fetchall(
                "SELECT name FROM characters WHERE novel_id = %s",
                (novel_id,),
                dict_rows=True,
            )
            close = difflib.get_close_matches(
                name, [r["name"] for r in rows], n=3, cutoff=0.5
            )
            hint = f"; closest names: {', '.join(close)}" if close else ""
            raise ValueError(f"Character not found: {name}{hint}")

        character_id = str(character["id"])

        chapter_filter = ""
        params: list[Any] = [character_id]
        if up_to_chapter is not None:
            chapter_filter = " AND ch.number <= %s"
            params.append(up_to_chapter)

        latest_state = db.fetchone(
            f"""
            SELECT ch.number AS chapter_number,
                   l.name AS location,
                   cs.emotional_state,
                   cs.goals,
                   cs.knowledge,
                   cs.relationships,
                   cs.physical_state,
                   cs.notes
            FROM character_states cs
            JOIN chapters ch ON ch.id = cs.chapter_id
            LEFT JOIN locations l ON l.id = cs.location_id
            WHERE cs.character_id = %s
            {chapter_filter}
            ORDER BY ch.number DESC
            LIMIT 1
            """,
            tuple(params),
            dict_rows=True,
        )

        history = db.fetchall(
            f"""
            SELECT ch.number AS chapter_number,
                   l.name AS location,
                   cs.emotional_state,
                   cs.goals,
                   cs.knowledge,
                   cs.relationships,
                   cs.physical_state,
                   cs.notes
            FROM character_states cs
            JOIN chapters ch ON ch.id = cs.chapter_id
            LEFT JOIN locations l ON l.id = cs.location_id
            WHERE cs.character_id = %s
            {chapter_filter}
            ORDER BY ch.number ASC
            """,
            tuple(params),
            dict_rows=True,
        )

        events_params: list[Any] = [character_id]
        events_filter = ""
        if up_to_chapter is not None:
            events_filter = " AND ch.number <= %s"
            events_params.append(up_to_chapter)

        events = db.fetchall(
            f"""
            SELECT e.id,
                   ch.number AS chapter_number,
                   e.description,
                   e.event_type,
                   e.impact_level
            FROM events e
            JOIN chapters ch ON ch.id = e.chapter_id
            WHERE %s::uuid = ANY(e.involved_characters)
            {events_filter}
            ORDER BY ch.number, e.created_at
            """,
            tuple(events_params),
            dict_rows=True,
        )

        rel_params: list[Any] = [character_id, character_id]
        rel_filter = ""
        if up_to_chapter is not None:
            rel_filter = " AND ch.number <= %s"
            rel_params.append(up_to_chapter)

        relationships = db.fetchall(
            f"""
            SELECT r.entity_a_id,
                   r.entity_b_id,
                   r.entity_a_type,
                   r.entity_b_type,
                   r.rel_type,
                   r.status,
                   r.notes,
                   ch.number AS chapter_number,
                   ca.name AS entity_a_name,
                   cb.name AS entity_b_name
            FROM relationships r
            LEFT JOIN chapters ch ON ch.id = r.chapter_id
            LEFT JOIN characters ca ON ca.id = r.entity_a_id
            LEFT JOIN characters cb ON cb.id = r.entity_b_id
            WHERE (r.entity_a_id = %s OR r.entity_b_id = %s)
            {rel_filter}
            ORDER BY ch.number NULLS LAST, r.created_at
            """,
            tuple(rel_params),
            dict_rows=True,
        )

        return {
            "identity": {
                "id": character_id,
                "name": character["name"],
                "aliases": character["aliases"] or [],
                "first_appearance_chapter": character["first_appearance_chapter"],
                "description": character["description"],
            },
            "current_state": dict(latest_state) if latest_state else None,
            "history": [dict(row) for row in history],
            "relationships": [dict(row) for row in relationships],
            "events": [dict(row) for row in events],
            "spoiler_cap": up_to_chapter,
        }


def build_relationship_graph(
    novel_id: str,
    *,
    up_to_chapter: int | None = None,
) -> dict[str, Any]:
    with DBClient() as db:
        params: list[Any] = [novel_id]
        chapter_filter = ""
        if up_to_chapter is not None:
            chapter_filter = "AND (r.from_chapter IS NULL OR r.from_chapter <= %s)"
            params.append(up_to_chapter)

        char_rows = db.fetchall(
            """
            SELECT c.id, e.name
            FROM entities e
            JOIN characters c ON c.entity_id = e.id
            WHERE e.novel_id = %s AND e.entity_type = 'character'
            ORDER BY e.name
            """,
            (novel_id,),
            dict_rows=True,
        )
        nodes = [{"id": str(row["id"]), "label": row["name"]} for row in char_rows]
        character_ids = {row["id"] for row in char_rows}

        edge_rows = db.fetchall(
            f"""
            SELECT r.id, ca.id AS char_a_id, cb.id AS char_b_id,
                   r.rel_type, r.from_chapter, r.notes
            FROM relationships r
            JOIN entities ea ON ea.id = r.entity_a_id AND ea.novel_id = %s AND ea.entity_type = 'character'
            JOIN entities eb ON eb.id = r.entity_b_id AND eb.entity_type = 'character'
            JOIN characters ca ON ca.entity_id = ea.id
            JOIN characters cb ON cb.entity_id = eb.id
            {chapter_filter}
            ORDER BY r.from_chapter NULLS LAST, r.created_at
            """,
            tuple(params),
            dict_rows=True,
        )

        return {
            "nodes": nodes,
            "edges": [
                {
                    "id": str(row["id"]),
                    "source": str(row["char_a_id"]),
                    "target": str(row["char_b_id"]),
                    "rel_type": row["rel_type"],
                    "chapter_number": row["from_chapter"],
                    "notes": row["notes"],
                }
                for row in edge_rows
                if row["char_a_id"] in character_ids and row["char_b_id"] in character_ids
            ],
            "up_to_chapter": up_to_chapter,
        }


def list_timeline(novel_id: str) -> list[dict[str, Any]]:
    with DBClient() as db:
        rows = db.fetchall(
            """
            SELECT t.id, t.description, t.story_date, t.sort_order,
                   t.involved_characters, t.involved_locations,
                   t.involved_objects, t.involved_factions
            FROM timeline t
            WHERE t.novel_id = %s
            ORDER BY t.sort_order, t.created_at
            """,
            (novel_id,),
            dict_rows=True,
        )

        char_rows = db.fetchall(
            "SELECT id, name FROM characters WHERE novel_id = %s", (novel_id,), dict_rows=True
        )
        char_name = {str(r["id"]): r["name"] for r in char_rows}

        result = []
        for r in rows:
            result.append(
                {
                    "id": str(r["id"]),
                    "description": r["description"],
                    "story_date": r["story_date"],
                    "sort_order": r["sort_order"],
                    "involved_characters": [char_name.get(str(c), str(c)) for c in (r["involved_characters"] or [])],
                }
            )
        return result


def list_open_threads(
    novel_id: str, up_to_chapter: int, *, db: DBClient | None = None
) -> list[dict[str, Any]]:
    """Plot threads opened by `up_to_chapter` and not yet closed at that point."""
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        threads = client.fetchall(
            """
            SELECT pt.id, pt.title, pt.description, pt.status, pt.thread_type,
                   pt.opened_chapter, pt.closed_chapter
            FROM plot_threads pt
            WHERE pt.novel_id = %s
              AND (pt.opened_chapter IS NULL OR pt.opened_chapter <= %s)
              AND (pt.closed_chapter IS NULL OR pt.closed_chapter > %s)
            ORDER BY pt.opened_chapter NULLS LAST, pt.title
            """,
            (novel_id, up_to_chapter, up_to_chapter),
            dict_rows=True,
        )
        payload: list[dict[str, Any]] = []
        for thread in threads:
            events = client.fetchall(
                """
                SELECT te.impact, e.description, e.event_type, e.impact_level,
                       ch.number AS chapter_number
                FROM thread_events te
                JOIN events e ON e.id = te.event_id
                JOIN chapters ch ON ch.id = e.chapter_id
                WHERE te.thread_id = %s AND ch.number <= %s
                ORDER BY ch.number ASC, e.created_at ASC
                """,
                (thread["id"], up_to_chapter),
                dict_rows=True,
            )
            payload.append({**dict(thread), "events": [dict(e) for e in events]})
        return payload
    finally:
        if owned:
            client.close()


def search_story(
    novel_id: str,
    query_text: str,
    writing_chapter: int,
    *,
    k: int = 8,
    retriever: HybridRetriever | None = None,
) -> dict[str, Any]:
    """Hybrid semantic+keyword search over chapters <= writing_chapter - 1."""
    owned_db: DBClient | None = None
    if retriever is None:
        owned_db = DBClient()
        retriever = HybridRetriever(
            owned_db, EmbeddingService(use_mock=settings.use_mock_llm)
        )
    try:
        bundle = retriever.retrieve(
            RetrievalQuery(
                text=query_text,
                novel_id=novel_id,
                max_chapter=writing_chapter - 1,
                k=k,
            ),
            use_rerank=False,
        )
        return {
            "results": [
                {
                    "kind": str(r.kind),
                    "chapter_number": r.chapter_number,
                    "score": r.score,
                    "snippet": r.snippet,
                }
                for r in bundle.results
            ]
        }
    finally:
        if owned_db is not None:
            owned_db.close()


def _finding_dict(finding: Any) -> dict[str, Any]:
    return {
        "check": finding.check,
        "message": finding.message,
        "quote": finding.quote,
        "suggested_fix": finding.suggested_fix,
    }


def check_continuity(
    novel_id: str,
    chapter_number: int,
    draft_text: str,
    *,
    db: DBClient | None = None,
    use_mock: bool | None = None,
) -> dict[str, Any]:
    """Run claim extraction + the ContinuityCritic on a draft (not saved)."""
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        raw_claims = extract_draft_claims(draft_text, use_mock=use_mock)
        draft = build_draft_chapter(
            client,
            novel_id=novel_id,
            chapter_number=chapter_number,
            text=draft_text,
            raw_claims=raw_claims,
            planned_thread_ids=[],
            planned_commitment_ids=[],
        )
        report = ContinuityCritic(client).critique(draft)
        return {
            "passed": report.passed,
            "fails": [_finding_dict(f) for f in report.fails],
            "warns": [_finding_dict(f) for f in report.warns],
        }
    finally:
        if owned:
            client.close()


def save_chapter(
    novel_id: str,
    chapter_number: int,
    text: str,
    title: str | None = None,
    *,
    db: DBClient | None = None,
) -> dict[str, Any]:
    """Ingest a finished draft as source='generated'. Never overwrites."""
    if not text or not text.strip():
        return {"error": "text must not be empty"}
    try:
        outcome = process_chapter(
            novel_id=novel_id,
            chapter_number=chapter_number,
            raw_text=text,
            chapter_title=title,
            use_mock_llm=None,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            db=db,
            replace=False,
            source="generated",
            generation_meta={"via": "mcp"},
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"ingested": True, "chapter_id": str(outcome.get("chapter_id"))}
