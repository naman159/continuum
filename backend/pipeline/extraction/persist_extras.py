from __future__ import annotations

"""Persistence helpers for the Phase 3 extraction passes.

Scenes, knowledge, commitments, and summaries share the chapter transaction.
Database and embedding failures propagate to the coordinator and roll it back.
Unresolved references are reported as enrichment warnings to the caller.
"""

from difflib import SequenceMatcher
from typing import Any

from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, vector_literal
from pipeline.extraction.resolver import EntityResolver



# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------


def persist_scenes(
    db: DBClient,
    *,
    chapter_id: str,
    scenes_data: list[dict[str, Any]],
    resolver: EntityResolver,
    embedder: EmbeddingService,
    warnings: list[str] | None = None,
) -> list[str]:
    """Insert scene rows for the chapter. Returns inserted scene IDs."""
    inserted: list[str] = []
    for idx, scene in enumerate(scenes_data or []):
        if not isinstance(scene, dict):
            continue

        try:
            scene_index = int(scene.get("scene_index", idx))
        except (TypeError, ValueError):
            scene_index = idx

        pov_name = (scene.get("pov_character_name") or "").strip() or None
        pov_character_id = None
        if pov_name:
            # Reference-only: resolve against existing characters, never mint.
            resolved_pov = resolver.resolve_character(pov_name, create=False)
            pov_character_id = resolved_pov.entity_id if resolved_pov else None
            if resolved_pov is None and warnings is not None:
                warnings.append(f"Scene {scene_index}: unknown point-of-view character {pov_name!r}.")

        location_name = (scene.get("location_name") or "").strip() or None
        location_id = None
        if location_name:
            location_id = resolver.resolve_location(location_name).entity_id

        present_ids: list[str] = []
        for name in scene.get("present_character_names", []) or []:
            cleaned = str(name).strip()
            if not cleaned:
                continue
            resolved_present = resolver.resolve_character(cleaned, create=False)
            if resolved_present is not None:
                present_ids.append(resolved_present.entity_id)
            elif warnings is not None:
                warnings.append(f"Scene {scene_index}: unknown participant {cleaned!r}.")

        summary = str(scene.get("summary", "")).strip()
        time_anchor = (scene.get("time_anchor") or "").strip() or None

        embedding_vec = vector_literal(embedder.embed_text(summary)) if summary else None

        scene_id = db.fetchval(
            """
            INSERT INTO scenes (
                chapter_id, scene_index, pov_character_id, location_id,
                time_anchor, present_characters, summary, embedding,
                embedding_model
            )
            VALUES (
                %s, %s, %s, %s, %s, %s::uuid[], %s,
                CASE WHEN %s::text IS NULL THEN NULL ELSE %s::vector END,
                %s
            )
            ON CONFLICT (chapter_id, scene_index) DO UPDATE SET
                pov_character_id = EXCLUDED.pov_character_id,
                location_id = EXCLUDED.location_id,
                time_anchor = EXCLUDED.time_anchor,
                present_characters = EXCLUDED.present_characters,
                summary = EXCLUDED.summary,
                embedding = EXCLUDED.embedding,
                embedding_model = EXCLUDED.embedding_model
            RETURNING id
            """,
            (
                chapter_id,
                scene_index,
                pov_character_id,
                location_id,
                time_anchor,
                present_ids,
                summary,
                embedding_vec,
                embedding_vec,
                embedder.model_name if embedding_vec else None,
            ),
            commit=True,
        )
        inserted.append(str(scene_id))

    return inserted


# ---------------------------------------------------------------------------
# Multi-granularity summaries
# ---------------------------------------------------------------------------


def persist_multi_summaries(
    db: DBClient,
    *,
    chapter_id: str,
    summary_short: str,
    summary_medium: str,
    summary_long: str,
    embedder: EmbeddingService | None = None,
) -> None:
    """Update chapters with the three summary granularities and re-embed."""
    short = (summary_short or "").strip()
    medium = (summary_medium or "").strip()
    long_ = (summary_long or "").strip()

    if embedder is not None and medium:
        embedding_vec = vector_literal(embedder.embed_text(medium))
        db.execute(
            """
            UPDATE chapters
            SET summary_short = %s,
                summary = COALESCE(NULLIF(%s, ''), summary),
                summary_long = %s,
                embedding = %s::vector,
                embedding_model = %s
            WHERE id = %s
            """,
            (short or None, medium, long_ or None, embedding_vec,
             embedder.model_name, chapter_id),
        )
    else:
        db.execute(
            """
            UPDATE chapters
            SET summary_short = %s,
                summary = COALESCE(NULLIF(%s, ''), summary),
                summary_long = %s
            WHERE id = %s
            """,
            (short or None, medium, long_ or None, chapter_id),
        )


# ---------------------------------------------------------------------------
# Knowledge edges
# ---------------------------------------------------------------------------


_CERTAINTY_MAP = {"high": 1.0, "medium": 0.6, "low": 0.3}
_VALID_SOURCE_TYPES = {
    "dialogue",
    "observation",
    "inference",
    "witnessed",
    "told",
    "assumed",
}


def persist_knows_edges(
    db: DBClient,
    *,
    chapter_number: int,
    learnings: list[dict[str, Any]],
    resolver: EntityResolver,
    warnings: list[str] | None = None,
) -> list[str]:
    """Insert knows_edges rows for each character learning."""
    inserted: list[str] = []
    for learning in learnings or []:
        if not isinstance(learning, dict):
            continue
        character_name = str(learning.get("character_name", "")).strip()
        fact = str(learning.get("fact_description", "")).strip()
        if not character_name or not fact:
            continue

        # Reference-only: only existing characters can hold knowledge.
        resolved_knower = resolver.resolve_character(character_name, create=False)
        if resolved_knower is None:
            if warnings is not None:
                warnings.append(f"Knowledge skipped: unknown character {character_name!r}.")
            continue
        character_id = resolved_knower.entity_id

        source_type = str(learning.get("source_type", "")).strip().lower()
        if source_type not in _VALID_SOURCE_TYPES:
            source_type = None

        certainty_raw = learning.get("certainty")
        if isinstance(certainty_raw, (int, float)):
            certainty = float(certainty_raw)
        else:
            certainty = _CERTAINTY_MAP.get(
                str(certainty_raw or "").strip().lower(), 1.0
            )

        shared_ids: list[str] = []
        for name in learning.get("shared_with_character_names", []) or []:
            cleaned = str(name).strip()
            if not cleaned:
                continue
            resolved_shared = resolver.resolve_character(cleaned, create=False)
            if resolved_shared is not None:
                shared_ids.append(resolved_shared.entity_id)
            elif warnings is not None:
                warnings.append(f"Knowledge sharing: unknown character {cleaned!r}.")

        edge_id = db.fetchval(
            """
            INSERT INTO knows_edges (
                character_id, fact_description, learned_chapter,
                source_type, certainty, shared_with
            )
            VALUES (%s, %s, %s, %s, %s, %s::uuid[])
            RETURNING id
            """,
            (
                character_id,
                fact,
                chapter_number,
                source_type,
                certainty,
                shared_ids,
            ),
            commit=True,
        )
        inserted.append(str(edge_id))

    return inserted


# ---------------------------------------------------------------------------
# Commitments (CFPG foreshadow / payoff)
# ---------------------------------------------------------------------------


_WEIGHT_MAP = {"low": 0.3, "medium": 0.6, "high": 1.0}


def _resolve_related_entities(
    names: list[str], resolver: EntityResolver, warnings: list[str] | None = None
) -> list[str]:
    ids: list[str] = []
    for name in names or []:
        cleaned = str(name).strip()
        if not cleaned:
            continue
        # Reference-only: link to existing entities, don't mint characters.
        uid = resolver.resolve_any_entity(cleaned, create=False)
        if uid is not None:
            ids.append(uid)
        elif warnings is not None:
            warnings.append(f"Commitment: unknown related entity {cleaned!r}.")
    return ids


def persist_commitments(
    db: DBClient,
    *,
    novel_id: str,
    chapter_number: int,
    foreshadows: list[dict[str, Any]],
    payoffs: list[dict[str, Any]],
    resolver: EntityResolver,
    embedder: EmbeddingService,
    warnings: list[str] | None = None,
) -> dict[str, list[str]]:
    """Persist foreshadow rows; match payoffs against pending commitments.

    Returns a dict with two lists of commitment IDs: ``inserted`` and
    ``satisfied`` (those updated to status='satisfied' by a payoff).
    """
    inserted: list[str] = []
    for fs in foreshadows or []:
        if not isinstance(fs, dict):
            continue
        text = str(fs.get("foreshadow_text", "")).strip()
        if not text:
            continue
        trigger = fs.get("trigger_predicate")
        if trigger is not None and not isinstance(trigger, str):
            trigger = str(trigger)
        weight_raw = str(fs.get("weight", "")).strip().lower()
        weight = _WEIGHT_MAP.get(weight_raw, 0.6)
        related_ids = _resolve_related_entities(
            fs.get("related_entity_names", []) or [], resolver, warnings
        )
        embedding_vec = vector_literal(embedder.embed_text(text))

        cid = db.fetchval(
            """
            INSERT INTO commitments (
                novel_id, foreshadow_text, foreshadow_chapter,
                trigger_predicate, status, weight,
                related_entity_ids, embedding, embedding_model
            )
            VALUES (
                %s, %s, %s,
                CASE WHEN %s::text IS NULL THEN NULL ELSE to_jsonb(%s::text) END,
                'pending', %s, %s::uuid[], %s::vector, %s
            )
            RETURNING id
            """,
            (
                novel_id,
                text,
                chapter_number,
                trigger,
                trigger,
                weight,
                related_ids,
                embedding_vec,
                embedder.model_name,
            ),
            commit=True,
        )
        inserted.append(str(cid))

    # Payoffs: match against pending commitments via fuzzy text similarity.
    satisfied: list[str] = []
    if payoffs:
        pending = db.fetchall(
            """
            SELECT id, foreshadow_text
            FROM commitments
            WHERE novel_id = %s AND status = 'pending'
            """,
            (novel_id,),
            dict_rows=True,
        ) or []

        for payoff in payoffs:
            if not isinstance(payoff, dict):
                continue
            payoff_text = str(payoff.get("payoff_text", "")).strip()
            if not payoff_text:
                continue
            matches_text = str(payoff.get("matches_foreshadow", "") or "").strip()

            target_id = _best_pending_match(pending, matches_text, payoff_text)
            if not target_id:
                if warnings is not None:
                    warnings.append(f"Payoff could not be matched to a pending commitment: {payoff_text}")
                continue
            db.execute(
                """
                UPDATE commitments
                SET status = 'satisfied',
                    payoff_chapter = %s,
                    payoff_text = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (chapter_number, payoff_text, target_id),
            )
            satisfied.append(target_id)
            # Drop it from the in-memory pending list so a second payoff
            # doesn't latch onto the same row.
            pending = [row for row in pending if str(row["id"]) != target_id]

    return {"inserted": inserted, "satisfied": satisfied}


def _best_pending_match(
    pending_rows: list[dict[str, Any]],
    matches_text: str,
    payoff_text: str,
) -> str | None:
    if not pending_rows:
        return None

    best_id: str | None = None
    best_score = 0.0
    needle_primary = matches_text.lower() if matches_text else ""
    needle_fallback = payoff_text.lower()

    for row in pending_rows:
        fs_text = str(row.get("foreshadow_text", "")).lower()
        if not fs_text:
            continue
        score_primary = (
            SequenceMatcher(None, needle_primary, fs_text).ratio()
            if needle_primary
            else 0.0
        )
        score_fallback = SequenceMatcher(None, needle_fallback, fs_text).ratio() * 0.6
        score = max(score_primary, score_fallback)
        if score > best_score:
            best_score = score
            best_id = str(row["id"])

    # Require a reasonable similarity to avoid wild matches.
    if best_score < 0.45:
        return None
    return best_id


__all__ = [
    "persist_scenes",
    "persist_multi_summaries",
    "persist_knows_edges",
    "persist_commitments",
]
