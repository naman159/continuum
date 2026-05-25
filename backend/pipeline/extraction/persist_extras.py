from __future__ import annotations

"""Persistence helpers for the Phase 3 extraction passes.

These functions are called from ``pipeline.pipeline.process_chapter`` after
the core extraction has been persisted. They populate the SOTA tables added
on the closing-the-gap-with-sota branch: ``scenes``, ``knows_edges``, and
``commitments``, plus the multi-granularity summary columns on ``chapters``.
"""

import logging
from difflib import SequenceMatcher
from typing import Any

from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, vector_literal
from pipeline.extraction.resolver import EntityResolver

logger = logging.getLogger(__name__)


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
            try:
                pov_character_id = resolver.resolve_character(pov_name).entity_id
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("scene pov resolve failed (%s): %s", pov_name, exc)

        location_name = (scene.get("location_name") or "").strip() or None
        location_id = None
        if location_name:
            try:
                location_id = resolver.resolve_location(location_name).entity_id
            except Exception as exc:  # pragma: no cover
                logger.warning("scene location resolve failed (%s): %s", location_name, exc)

        present_ids: list[str] = []
        for name in scene.get("present_character_names", []) or []:
            cleaned = str(name).strip()
            if not cleaned:
                continue
            try:
                present_ids.append(resolver.resolve_character(cleaned).entity_id)
            except Exception as exc:  # pragma: no cover
                logger.warning("scene present resolve failed (%s): %s", cleaned, exc)

        summary = str(scene.get("summary", "")).strip()
        time_anchor = (scene.get("time_anchor") or "").strip() or None

        embedding_vec = vector_literal(embedder.embed_text(summary)) if summary else None

        try:
            scene_id = db.fetchval(
                """
                INSERT INTO scenes (
                    chapter_id, scene_index, pov_character_id, location_id,
                    time_anchor, present_characters, summary, embedding
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s::uuid[], %s,
                    CASE WHEN %s::text IS NULL THEN NULL ELSE %s::vector END
                )
                ON CONFLICT (chapter_id, scene_index) DO UPDATE SET
                    pov_character_id = EXCLUDED.pov_character_id,
                    location_id = EXCLUDED.location_id,
                    time_anchor = EXCLUDED.time_anchor,
                    present_characters = EXCLUDED.present_characters,
                    summary = EXCLUDED.summary,
                    embedding = EXCLUDED.embedding
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
                ),
                commit=True,
            )
            inserted.append(str(scene_id))
        except Exception as exc:  # pragma: no cover - log and continue
            logger.warning("scene insert failed: %s", exc)

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
                embedding = %s::vector
            WHERE id = %s
            """,
            (short or None, medium, long_ or None, embedding_vec, chapter_id),
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

        try:
            character_id = resolver.resolve_character(character_name).entity_id
        except Exception as exc:  # pragma: no cover
            logger.warning("knows_edge character resolve failed (%s): %s", character_name, exc)
            continue

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
            try:
                shared_ids.append(resolver.resolve_character(cleaned).entity_id)
            except Exception as exc:  # pragma: no cover
                logger.warning("knows_edge shared resolve failed (%s): %s", cleaned, exc)

        try:
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
        except Exception as exc:  # pragma: no cover
            logger.warning("knows_edge insert failed: %s", exc)

    return inserted


# ---------------------------------------------------------------------------
# Commitments (CFPG foreshadow / payoff)
# ---------------------------------------------------------------------------


_WEIGHT_MAP = {"low": 0.3, "medium": 0.6, "high": 1.0}


def _resolve_related_entities(
    names: list[str], resolver: EntityResolver
) -> list[str]:
    ids: list[str] = []
    for name in names or []:
        cleaned = str(name).strip()
        if not cleaned:
            continue
        try:
            ids.append(resolver.resolve_any_entity(cleaned))
        except Exception as exc:  # pragma: no cover
            logger.warning("commitment related resolve failed (%s): %s", cleaned, exc)
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
            fs.get("related_entity_names", []) or [], resolver
        )
        embedding_vec = vector_literal(embedder.embed_text(text))

        try:
            cid = db.fetchval(
                """
                INSERT INTO commitments (
                    novel_id, foreshadow_text, foreshadow_chapter,
                    trigger_predicate, status, weight,
                    related_entity_ids, embedding
                )
                VALUES (
                    %s, %s, %s,
                    CASE WHEN %s::text IS NULL THEN NULL ELSE to_jsonb(%s::text) END,
                    'pending', %s, %s::uuid[], %s::vector
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
                ),
                commit=True,
            )
            inserted.append(str(cid))
        except Exception as exc:  # pragma: no cover
            logger.warning("commitment insert failed: %s", exc)

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
                continue
            try:
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
            except Exception as exc:  # pragma: no cover
                logger.warning("commitment payoff update failed: %s", exc)

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
