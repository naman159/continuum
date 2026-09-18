from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pipeline.config import settings
from pipeline.critic.persist import persist_critique
from pipeline.critic.service import critique_draft
from pipeline.db.client import DBClient, DBSession
from pipeline.embeddings import EmbeddingService, embed_chapter_and_events
from pipeline.extraction.canonicalizer import (
    EntityCanonicalizer,
    IntraExtractionDeduplicator,
    apply_merges_to_extraction,
    collect_names_by_type,
)
from pipeline.extraction.chunker import sliding_window_chunks
from pipeline.extraction.context_select import select_context_entities
from pipeline.extraction.extractor import ChapterExtractor
from pipeline.extraction.persist import persist_extraction
from pipeline.extraction.persist_canon import persist_canon_facts
from pipeline.extraction.persist_deltas import persist_state_deltas
from pipeline.extraction.persist_extras import (
    persist_commitments,
    persist_knows_edges,
    persist_multi_summaries,
    persist_scenes,
)
from pipeline.extraction.resolver import EntityResolver
from pipeline.ingestion.ingest import delete_chapter_data, ingest_chapter
from pipeline.submissions import park_draft, supersede_pending
from pipeline.state.materializer import StateMaterializer

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3


def _normalize_custom_entities(
    custom_entities: list[Any],
    custom_entity_types: list[dict],
) -> list[dict]:
    """Drop extracted custom entities whose type isn't registered for the novel
    and normalize the type to its registered casing. Unregistered types would
    otherwise create entities rows that no dedup pass or UI page ever sees."""
    registered = {
        str(t.get("name", "")).strip().lower(): str(t.get("name", "")).strip()
        for t in custom_entity_types or []
        if str(t.get("name", "")).strip()
    }
    normalized: list[dict] = []
    for item in custom_entities or []:
        if not isinstance(item, dict):
            continue
        raw_type = str(item.get("type", "")).strip()
        match = registered.get(raw_type.lower())
        if match is None:
            logger.warning(
                "custom entity %r has unregistered type %r — skipped",
                item.get("name"),
                raw_type,
            )
            continue
        item["type"] = match
        normalized.append(item)
    return normalized


def _schema_sql(schema_path: str | None = None) -> str:
    """schema.sql with the configured vector width substituted in."""
    if schema_path is None:
        schema_path = str(Path(__file__).parent / "db" / "schema.sql")
    sql = Path(schema_path).read_text(encoding="utf-8")
    return sql.replace("__EMBEDDING_DIM__", str(settings.embedding_dimensions))


def init_db(schema_path: str | None = None) -> None:
    sql = _schema_sql(schema_path)
    with DBClient() as db:
        with db.cursor(commit=True) as cur:
            cur.execute(sql)
            cur.execute("DELETE FROM schema_version")
            cur.execute(
                "INSERT INTO schema_version (version) VALUES (%s)",
                (SCHEMA_VERSION,),
            )
        _assert_embedding_dimension_matches(db)
    _assert_no_schema_drift(schema_path)


def _assert_no_schema_drift(schema_path: str | None = None) -> None:
    """Fail loudly when the live database no longer matches schema.sql.

    Applying schema.sql is not the same as conforming to it: every CREATE TABLE
    is IF NOT EXISTS, so a constraint added to a table body after a database
    was created never reaches it, and init-db reports success anyway. Checking
    afterward is what turns that silent divergence into a message.
    """
    from pipeline.db.schema_drift import format_drift, schema_drift

    drift = schema_drift(schema_path)
    if drift:
        raise SystemExit(format_drift(drift))


def _assert_embedding_dimension_matches(db: DBClient) -> None:
    """Fail loudly when EMBEDDING_DIMENSIONS drifts from the live schema.

    Vector columns are created with the configured size baked in, but every
    CREATE TABLE is IF NOT EXISTS, so re-running init-db against a populated
    database silently leaves the old width in place. Every subsequent chapter
    then dies on `%s::vector` with an error that never mentions the config
    change that caused it.
    """
    live = db.fetchval(
        """
        SELECT atttypmod
        FROM pg_attribute
        WHERE attrelid = 'chapters'::regclass AND attname = 'embedding'
        """
    )
    if live is None:
        return
    if int(live) != settings.embedding_dimensions:
        raise SystemExit(
            f"EMBEDDING_DIMENSIONS is {settings.embedding_dimensions} but "
            f"chapters.embedding is vector({int(live)}). CREATE TABLE IF NOT "
            "EXISTS cannot widen an existing column: either restore the old "
            "value, or migrate the vector columns and re-embed."
        )


def create_novel(title: str, author: str | None, language: str) -> str:
    with DBClient() as db:
        novel_id = db.fetchval(
            """
            INSERT INTO novels (title, author, language)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (title, author, language),
            commit=True,
        )
        return str(novel_id)


def list_novels() -> list[dict[str, Any]]:
    with DBClient() as db:
        rows = db.fetchall(
            """
            SELECT id, title, author, language, created_at
            FROM novels
            ORDER BY created_at DESC
            """,
            dict_rows=True,
        )
        return [dict(row) for row in rows]


def load_story_context(
    db: DBClient,
    novel_id: str,
    chapter_number: int,
    custom_entity_types: list[dict] | None = None,
    chapter_text: str = "",
) -> dict[str, Any]:
    characters = db.fetchall(
        """
        SELECT c.id, c.name, c.aliases,
               ls.emotional_state, ls.goals, ls.physical_state, ls.last_chapter
        FROM characters c
        LEFT JOIN LATERAL (
            SELECT cs.emotional_state, cs.goals, cs.physical_state, ch.number AS last_chapter
            FROM character_states cs
            JOIN chapters ch ON ch.id = cs.chapter_id
            WHERE cs.character_id = c.id
              AND ch.number < %s
            ORDER BY ch.number DESC
            LIMIT 1
        ) ls ON true
        WHERE c.novel_id = %s
        ORDER BY c.name
        """,
        (chapter_number, novel_id),
        dict_rows=True,
    )

    locations = db.fetchall(
        """
        SELECT id, name, description, first_appearance_chapter
        FROM locations
        WHERE novel_id = %s
        ORDER BY name
        """,
        (novel_id,),
        dict_rows=True,
    )

    open_threads = db.fetchall(
        """
        SELECT id, title, description, status, thread_type, opened_chapter, closed_chapter
        FROM plot_threads
        WHERE novel_id = %s AND status <> 'closed'
        ORDER BY opened_chapter NULLS LAST, title
        """,
        (novel_id,),
        dict_rows=True,
    )

    recent_events = db.fetchall(
        """
        WITH max_number AS (
            SELECT COALESCE(MAX(number), 0) AS value
            FROM chapters
            WHERE novel_id = %s
              AND number < %s
        )
        SELECT e.id, e.description, e.event_type, e.impact_level, ch.number AS chapter_number
        FROM events e
        JOIN chapters ch ON ch.id = e.chapter_id
        CROSS JOIN max_number
        WHERE ch.novel_id = %s
          AND ch.number BETWEEN GREATEST(max_number.value - 2, 1) AND max_number.value
        ORDER BY ch.number DESC, e.created_at DESC
        LIMIT 200
        """,
        (novel_id, chapter_number, novel_id),
        dict_rows=True,
    )

    custom_entities: dict[str, list[dict]] = {}
    for et in (custom_entity_types or []):
        type_name = et["name"]
        rows = db.fetchall(
            "SELECT name FROM entities WHERE novel_id = %s AND entity_type = %s ORDER BY name",
            (novel_id, type_name),
            dict_rows=True,
        )
        custom_entities[type_name] = [{"name": r["name"]} for r in rows]

    character_rows = [dict(row) for row in characters]
    location_rows = [dict(row) for row in locations]
    if chapter_text:
        # Mentioned-in-chapter entities first, recency backfill, capped — keeps
        # prompt size bounded as the cast grows.
        character_rows = select_context_entities(
            chapter_text, character_rows, cap=settings.context_max_characters
        )
        location_rows = select_context_entities(
            chapter_text, location_rows, cap=settings.context_max_locations
        )

    return {
        "characters": character_rows,
        "locations": location_rows,
        "open_threads": [dict(row) for row in open_threads],
        "recent_events": [dict(row) for row in recent_events],
        "custom_entities": custom_entities,
    }


def analyze_chapter(
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    chapter_title: str | None,
    use_mock_llm: bool | None,
    chunk_size: int,
    chunk_overlap: int,
    progress: Any | None = None,
    db: DBClient | None = None,
    replace: bool = False,
    source: Literal["human", "agent"] = "human",
    run_critic: bool | None = None,
    on_continuity_fail: Literal["warn", "block"] = "warn",
    on_persist: Callable[[DBSession, str], None] | None = None,
) -> dict[str, Any]:
    """Ingest one chapter: CRITIQUE -> EXTRACT -> PERSIST -> MATERIALIZE.

    `source` is provenance only (it lands in `chapters.source`); it does not
    decide anything. What a continuity FAIL *means* is `on_continuity_fail`:

      "warn"  — record the findings and ingest anyway. The caller has a human
                in the loop already (the CLI, the Process page), so a finding
                is information, not a veto.
      "block" — refuse the write, park the draft for review, and return
                without extracting. For callers whose author is an agent that
                would otherwise write to canon unattended.

    Either way the critique runs exactly once, at phase 0, and its report is
    the one persisted at phase 5.

    on_persist joins caller-owned bookkeeping to the chapter transaction.
    If it raises, the chapter and those writes roll back together.
    """
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        # Fail fast on duplicates before paying for LLM extraction.
        existing = client.fetchval(
            "SELECT id FROM chapters WHERE novel_id = %s AND number = %s",
            (novel_id, chapter_number),
        )
        if existing is not None and not replace:
            raise ValueError(
                f"Chapter {chapter_number} already exists for novel {novel_id}. "
                "Pass replace=True to re-process it."
            )

        # ---- phase 0: CRITIQUE ----
        # The one place a draft is judged, for every caller. Runs before
        # EXTRACT so a blocking refusal costs one claims-extraction call
        # instead of 13 passes per chunk, and so refused text never reaches
        # the write tier (delete_chapter_data cannot undo entity creation).
        # Independent of `replace`: re-processing is exactly when a
        # contradiction is most likely.
        critique = None
        want_critique = settings.critic_enabled if run_critic is None else run_critic
        if on_continuity_fail == "block":
            # A caller that blocks on FAIL cannot opt out of the check that
            # produces the FAIL — otherwise run_critic=False is a bypass.
            want_critique = True
        if want_critique:
            critique = critique_draft(
                client,
                novel_id=novel_id,
                chapter_number=chapter_number,
                text=raw_text,
                use_mock_llm=use_mock_llm,
            )

        if on_continuity_fail == "block" and critique is not None and not critique.passed:
            # An outage ("unavailable": critic off, or mock mode) parks
            # nothing: there is no verdict for a human to adjudicate, so
            # telling the caller "pending_review" would claim a review that
            # isn't happening. A real FAIL, or an error while critiquing,
            # parks the text so the author's work isn't lost.
            if critique.status == "unavailable":
                logger.error(
                    "refusing write for novel %s ch %s: %s",
                    novel_id, chapter_number, critique.error,
                )
                return {
                    "ingested": False,
                    "status": "refused",
                    "submission_id": None,
                    "reason": critique.status,
                    "error": critique.error,
                    "fails": [],
                    "warns": [],
                }
            submission_id = park_draft(
                client,
                novel_id=novel_id,
                chapter_number=chapter_number,
                title=chapter_title,
                raw_text=raw_text,
                findings={
                    "fails": critique.fails,
                    "warns": critique.warns,
                    **({"error": critique.error} if critique.error else {}),
                },
            )
            return {
                "ingested": False,
                "status": "pending_review",
                "submission_id": submission_id,
                "reason": "fail" if critique.status == "ok" else critique.status,
                "fails": critique.fails,
                "warns": critique.warns,
            }

        custom_entity_types = [
            dict(r)
            for r in client.fetchall(
                "SELECT name, description FROM novel_entity_types WHERE novel_id = %s ORDER BY name",
                (novel_id,),
                dict_rows=True,
            )
        ]

        context = load_story_context(
            client,
            novel_id,
            chapter_number,
            custom_entity_types=custom_entity_types,
            chapter_text=raw_text,
        )
        chunks = sliding_window_chunks(raw_text, chunk_size=chunk_size, overlap=chunk_overlap)
        extractor = ChapterExtractor(use_mock=use_mock_llm)
        extracted = extractor.extract_chapter(
            chunks=chunks,
            context=context,
            progress=progress,
            custom_entity_types=custom_entity_types or None,
        )
        extracted["custom_entities"] = _normalize_custom_entities(
            extracted.get("custom_entities", []), custom_entity_types
        )

        if progress is not None:
            progress.on_pass_start("intra_dedup")
        deduplicator = IntraExtractionDeduplicator(use_mock=use_mock_llm)
        extracted = deduplicator.deduplicate(extracted, raw_text)
        if progress is not None:
            progress.on_pass_done("intra_dedup")

        # Canonicalizer alias writes are intentionally OUTSIDE the transaction
        # below: they're additive metadata, harmless if persistence later fails,
        # and re-processing resolves onto them.
        if progress is not None:
            progress.on_pass_start("canonicalization")
        canonicalizer = EntityCanonicalizer(client, novel_id=novel_id, use_mock=use_mock_llm)
        merges = canonicalizer.canonicalize(
            chapter_text=raw_text,
            candidate_names_by_type=collect_names_by_type(extracted),
        )
        # Rewrite merged candidates to their targets' canonical names so
        # every merge takes effect this chapter — for reasoning-only
        # (non-persisted-alias) merges this rename is the only mechanism.
        extracted = apply_merges_to_extraction(client, extracted, merges)
        if progress is not None:
            progress.on_pass_done("canonicalization")

        # ---- everything below is one transaction ----
        # The session pins one pooled connection across the embedding network
        # calls below; jobs.py limits processing to two worker threads.
        with client.session() as s:
            if replace:
                delete_chapter_data(s, novel_id=novel_id, chapter_number=chapter_number)
            chapter_id = ingest_chapter(
                s,
                novel_id=novel_id,
                chapter_number=chapter_number,
                title=chapter_title,
                raw_text=raw_text,
                source=source,
            )
            resolver = EntityResolver(s, novel_id=novel_id, chapter_number=chapter_number)
            event_rows = persist_extraction(
                s,
                resolver=resolver,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                extracted=extracted,
            )

            embedding_service = EmbeddingService(use_mock=use_mock_llm)
            embed_chapter_and_events(
                s,
                chapter_id=chapter_id,
                chapter_summary=extracted.get("summary", ""),
                event_rows=event_rows,
                service=embedding_service,
                # persist_multi_summaries re-embeds the chapter from summary_medium;
                # skip the throwaway embedding when that will happen.
                embed_chapter=not (extracted.get("summary_medium") or "").strip(),
            )

            s.execute(
                """
                UPDATE chapters
                SET summary = %s,
                    processed_at = now()
                WHERE id = %s
                """,
                (extracted.get("summary", ""), chapter_id),
            )
            persist_multi_summaries(
                s,
                chapter_id=chapter_id,
                summary_short=extracted.get("summary_short", ""),
                summary_medium=extracted.get("summary_medium", ""),
                summary_long=extracted.get("summary_long", ""),
                embedder=embedding_service,
            )
            persist_scenes(
                s,
                chapter_id=chapter_id,
                scenes_data=extracted.get("scenes", []),
                resolver=resolver,
                embedder=embedding_service,
            )
            persist_knows_edges(
                s,
                chapter_number=chapter_number,
                learnings=extracted.get("learnings", []),
                resolver=resolver,
            )
            persist_commitments(
                s,
                novel_id=novel_id,
                chapter_number=chapter_number,
                foreshadows=extracted.get("foreshadows_introduced", []),
                payoffs=extracted.get("payoffs_delivered", []),
                resolver=resolver,
                embedder=embedding_service,
            )
            persist_canon_facts(
                s,
                novel_id=novel_id,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                facts=extracted.get("canon_facts", []),
                resolver=resolver,
            )
            persist_state_deltas(
                s,
                chapter_id=chapter_id,
                deltas=extracted.get("state_deltas", []),
                resolver=resolver,
            )
            if on_continuity_fail == "block":
                # Resolve the parked draft only when its replacement commits.
                # Extraction or persistence failures must leave it reviewable.
                supersede_pending(
                    s,
                    novel_id=novel_id,
                    chapter_number=chapter_number,
                    note="superseded by a passing resubmission",
                )
            if on_persist is not None:
                on_persist(s, chapter_id)

        # ---- phase 4: MATERIALIZE / phase 5: RECORD THE CRITIQUE ----
        # Both derive from the committed data and are idempotent; a failure
        # here must not roll back the saved chapter. materialized/critique in
        # the result tell callers whether a manual re-run is needed.
        materialized = False
        critique_summary: dict[str, Any] | None = None
        try:
            StateMaterializer(client).materialize(novel_id)
            materialized = True
        except Exception:
            logger.exception("materialize failed for novel %s; re-run pipeline.state.cli", novel_id)
        # No second judgement: this persists the phase-0 report against the row
        # that now exists. The chapter could not get a critique_reports row
        # before it had a chapter_id, which is the only reason recording it is
        # a separate phase from producing it. An outage at phase 0 leaves
        # nothing to record — `python -m pipeline.critic.cli` supplies it later.
        if critique is not None:
            critique_summary = critique.summary()
            if critique.report is not None:
                try:
                    persist_critique(client, chapter_id=chapter_id, report=critique.report)
                except Exception:
                    critique_summary = {**critique_summary, "persisted": False}
                    logger.exception(
                        "recording the critique failed for chapter %s of novel %s; "
                        "re-run pipeline.critic.cli", chapter_number, novel_id,
                    )
                else:
                    critique_summary = {**critique_summary, "persisted": True}

        return {
            "chapter_id": chapter_id,
            "chunks": len(chunks),
            "summary_preview": extracted.get("summary", "")[:200],
            "new_characters": len(extracted.get("new_entities", {}).get("characters", [])),
            "new_locations": len(extracted.get("new_entities", {}).get("locations", [])),
            "events": len(extracted.get("events", [])),
            "state_deltas": len(extracted.get("state_deltas", [])),
            "thread_updates": len(extracted.get("thread_updates", [])),
            "continuity_flags": len(extracted.get("continuity_flags", [])),
            "materialized": materialized,
            "critique": critique_summary,
        }
    finally:
        if owned:
            client.close()
