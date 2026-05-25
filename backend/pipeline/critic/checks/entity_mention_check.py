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

from pipeline.critic.types import Finding, Severity
from pipeline.db.client import DBClient


def check_entity_mentions(
    db: DBClient,
    novel_id: str,
    mentions: list[dict],
) -> list[Finding]:
    if not mentions:
        return []

    entity_ids = list({m["entity_id"] for m in mentions if m.get("entity_id")})
    if not entity_ids:
        return []

    rows = db.fetchall(
        """
        SELECT subject_entity_id, predicate, value, locked, source_chapter, confidence
          FROM canon_facts
         WHERE novel_id = %s AND subject_entity_id = ANY(%s::uuid[])
        """,
        (novel_id, entity_ids),
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
        if claimed.lower() == str(canonical["value"]).strip().lower():
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
