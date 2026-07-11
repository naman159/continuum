"""Knowledge-state ("who knows what") check.

For each knowledge claim — a draft passage that implies a character knows
some fact — verify the character has a corresponding active knows_edges row
as of chapter_number - 1, OR the chapter contains an explicit
learn-this-chapter event. Without that, the character is "knowing" something
they shouldn't.

Inputs:
    knowledge_claims: [{character_id, fact_description, source_type, quote}]

The stored fact_description (written at ingest) and the draft claim's
fact_description come from two independent LLM calls, so they rarely match
verbatim. Matching therefore uses the critic's shared word-overlap helper;
embedding-similarity matching would be a future improvement.
"""

from __future__ import annotations

from pipeline.critic.types import Finding, Severity, words_overlap
from pipeline.critic.types import normalize_text as _normalize
from pipeline.db.client import DBClient


def _fact_is_known(fact: str, known_facts: set[str]) -> bool:
    """Exact, containment, or word-overlap match against known facts."""
    if fact in known_facts:
        return True
    for known in known_facts:
        if fact in known or known in fact:
            return True
        if words_overlap(fact, known, min_words=2, ratio=0.5):
            return True
    return False


def check_knowledge_state(
    db: DBClient,
    novel_id: str,
    chapter_number: int,
    knowledge_claims: list[dict],
) -> list[Finding]:
    if not knowledge_claims:
        return []

    char_ids = list(
        {str(k["character_id"]) for k in knowledge_claims if k.get("character_id")}
    )
    if not char_ids:
        return []

    # Pull active knows_edges per character.
    rows = db.fetchall(
        """
        SELECT character_id, fact_description, learned_chapter, source_type
          FROM knows_edges
         WHERE character_id = ANY(%s::uuid[])
           AND learned_chapter < %s
           AND superseded_by_id IS NULL
        """,
        (char_ids, chapter_number),
        dict_rows=True,
    )
    known: dict[str, set[str]] = {}
    for r in rows:
        known.setdefault(str(r["character_id"]), set()).add(
            _normalize(r["fact_description"])
        )

    findings: list[Finding] = []
    for k in knowledge_claims:
        cid = str(k["character_id"])
        fact = _normalize(k.get("fact_description", ""))
        if not fact:
            continue
        # learned_this_chapter is the authoritative signal that the character
        # acquires this fact within this draft, regardless of source_type —
        # inference/assumed are valid ways to learn something too, and gating
        # the exemption on a source_type allowlist FAILed those spuriously.
        if k.get("learned_this_chapter", True):
            continue
        # Otherwise the character must already know the fact.
        if not _fact_is_known(fact, known.get(cid, set())):
            findings.append(
                Finding(
                    check="knowledge_state",
                    severity=Severity.FAIL,
                    message=(
                        f"Character {cid} acts on knowledge they have not been "
                        f"shown to acquire: '{fact}'. No prior knows_edges row, "
                        f"no learn-this-chapter event."
                    ),
                    quote=k.get("quote"),
                    suggested_fix=(
                        "Either add a prior scene where the character learns this, "
                        "or rephrase the passage so the knowledge is acquired here."
                    ),
                    context={
                        "character_id": cid,
                        "fact_description": k.get("fact_description"),
                    },
                )
            )
    return findings
