from __future__ import annotations

"""Persistence for the canon_facts extraction pass.

Semantics:
- new (novel, subject, predicate)        -> INSERT
- existing, unlocked, conf >= existing   -> UPDATE value/confidence/source
- existing, unlocked, conf <  existing   -> skip (keep stronger fact)
- existing, LOCKED, same value           -> no-op
- existing, LOCKED, different value      -> continuity_flag (canon_contradiction)
"""

import logging
from typing import Any

from pipeline.db.client import DBClient
from pipeline.extraction.resolver import EntityResolver

logger = logging.getLogger(__name__)

_SUBJECT_RESOLVERS = {
    "character": "resolve_character",
    "location": "resolve_location",
    "object": "resolve_object",
    "faction": "resolve_faction",
}


def persist_canon_facts(
    db: DBClient,
    *,
    novel_id: str,
    chapter_id: str,
    chapter_number: int,
    facts: list[dict[str, Any]],
    resolver: EntityResolver,
) -> dict[str, int]:
    counts = {"inserted": 0, "updated": 0, "contradictions": 0, "skipped": 0}
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        subject_name = str(fact.get("subject_name", "")).strip()
        subject_type = str(fact.get("subject_type", "")).strip().lower()
        predicate = str(fact.get("predicate", "")).strip().lower()
        value = str(fact.get("value", "")).strip()
        if not subject_name or not predicate or not value:
            counts["skipped"] += 1
            continue
        method = _SUBJECT_RESOLVERS.get(subject_type)
        if method is None:
            logger.warning("canon fact subject_type %r not supported — skipped", subject_type)
            counts["skipped"] += 1
            continue
        subject_universal_id = getattr(resolver, method)(subject_name).universal_id

        try:
            confidence = max(0.0, min(1.0, float(fact.get("confidence") or 1.0)))
        except (TypeError, ValueError):
            confidence = 1.0
        kind = str(fact.get("kind") or "other").strip().lower()

        existing = db.fetchone(
            """
            SELECT id, value, locked, confidence
              FROM canon_facts
             WHERE novel_id = %s AND subject_entity_id = %s AND predicate = %s
            """,
            (novel_id, subject_universal_id, predicate),
            dict_rows=True,
        )

        if existing is None:
            # ON CONFLICT: a concurrent chapter job may have inserted the same (subject, predicate); losing this race must not abort the whole chapter transaction.
            db.execute(
                """
                INSERT INTO canon_facts (
                    novel_id, kind, subject_entity_id, predicate, value,
                    source_chapter, confidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (novel_id, subject_entity_id, predicate) DO NOTHING
                """,
                (novel_id, kind, subject_universal_id, predicate, value,
                 chapter_number, confidence),
            )
            counts["inserted"] += 1
            continue

        same_value = value.lower() == str(existing["value"]).strip().lower()
        if existing["locked"]:
            if not same_value:
                db.execute(
                    """
                    INSERT INTO continuity_flags (chapter_id, description, flag_type)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        chapter_id,
                        (
                            f"Chapter contradicts locked canon: {subject_name} "
                            f"{predicate} is locked to {existing['value']!r} but this "
                            f"chapter says {value!r}. Quote: {fact.get('quote') or 'n/a'}"
                        ),
                        "canon_contradiction",
                    ),
                )
                counts["contradictions"] += 1
            continue

        if confidence >= float(existing.get("confidence") or 0.0):
            db.execute(
                """
                UPDATE canon_facts
                   SET value = %s, confidence = %s, source_chapter = %s, kind = %s
                 WHERE id = %s
                """,
                (value, confidence, chapter_number, kind, existing["id"]),
            )
            counts["updated"] += 1
        else:
            # Never weaken an existing record: lower-confidence re-statements
            # (same or different value) are dropped.
            counts["skipped"] += 1
    return counts


__all__ = ["persist_canon_facts"]
