"""Entity-mention check.

For every entity referenced in the draft, look up the locked canon facts and
flag any mention that contradicts a locked fact. Mention payload shape:

    {
      "entity_id": str,
      "predicate": str,      # e.g. "eye_color", "role", "title"
      "claimed_value": str,  # what the draft says
      "quote": str,
    }
"""

from __future__ import annotations

import re

from pipeline.critic.types import Finding, Severity, normalize_text
from pipeline.db.client import DBClient


def _canon_equivalent(a: str, b: str) -> bool:
    """Compare two canon values for practical equality.

    This check emits Severity.FAIL, so exact string equality made it fail a
    chapter on punctuation alone — canon "storm-grey" versus a draft's "storm
    grey" is not a continuity violation. Fold separators and surrounding
    punctuation before comparing.
    """
    def fold(value: str) -> str:
        collapsed = re.sub(r"[-_/]+", " ", normalize_text(value))
        return " ".join(re.sub(r"[^\w\s]", "", collapsed).split())

    return fold(a) == fold(b)


def check_entity_mentions(
    db: DBClient,
    novel_id: str,
    mentions: list[dict],
    chapter_number: int | None = None,
) -> list[Finding]:
    if not mentions:
        return []

    entity_ids = list({m["entity_id"] for m in mentions if m.get("entity_id")})
    if not entity_ids:
        return []

    # Compare against canon established BEFORE this chapter. On the ingestion
    # spine persist_canon_facts has already written this chapter's own facts by
    # the time the critique runs, so without this bound every mention is
    # compared against the row it just produced and the check can never
    # disagree with itself.
    rows = db.fetchall(
        """
        SELECT subject_entity_id, predicate, value, locked, source_chapter, confidence
          FROM canon_facts
         WHERE novel_id = %s AND subject_entity_id = ANY(%s::uuid[])
           AND (%s::int IS NULL OR source_chapter IS NULL OR source_chapter < %s::int)
        """,
        (novel_id, entity_ids, chapter_number, chapter_number),
        dict_rows=True,
    )

    # Index facts by (entity_id, predicate)
    fact_index: dict[tuple[str, str], dict] = {
        (str(r["subject_entity_id"]), r["predicate"]): r for r in rows
    }

    findings: list[Finding] = []
    for m in mentions:
        eid = m.get("entity_id")
        pred = m.get("predicate")
        if not eid or not pred:
            continue
        claimed = (m.get("claimed_value") or "").strip()
        if not claimed:
            continue
        key = (str(eid), pred)
        canonical = fact_index.get(key)
        if canonical is None:
            continue
        if _canon_equivalent(claimed, str(canonical["value"])):
            continue
        # Mismatch.
        sev = Severity.FAIL if canonical["locked"] else Severity.WARN
        findings.append(
            Finding(
                check="entity_mention",
                severity=sev,
                message=(
                    f"Mention contradicts {'locked ' if canonical['locked'] else ''}"
                    f"canon fact for entity {eid} ({pred}): "
                    f"draft says '{claimed}', canon says '{canonical['value']}' "
                    f"(set in chapter {canonical.get('source_chapter')})."
                ),
                quote=m.get("quote"),
                suggested_fix=(
                    f"Change '{claimed}' to '{canonical['value']}' "
                    f"or update canon (if facts have evolved and the fact is unlocked)."
                ),
                context={
                    "entity_id": str(eid),
                    "predicate": pred,
                    "canon_value": canonical["value"],
                    "claimed_value": claimed,
                    "locked": canonical["locked"],
                    "source_chapter": canonical.get("source_chapter"),
                },
            )
        )
    return findings
