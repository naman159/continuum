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
verbatim. Matching therefore uses word overlap (the same heuristic as
commitment_check); embedding-similarity matching would be a future
improvement.
"""

from __future__ import annotations

from pipeline.critic.types import Finding, Severity
from pipeline.db.client import DBClient


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _fact_is_known(fact: str, known_facts: set[str]) -> bool:
    """Exact, containment, or word-overlap match against known facts."""
    if fact in known_facts:
        return True
    claim_words = set(fact.split())
    if not claim_words:
        return False
    for known in known_facts:
        if fact in known or known in fact:
            return True
        overlap = claim_words & set(known.split())
        if len(overlap) >= max(2, int(len(claim_words) * 0.5)):
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
        # If the draft itself indicates the character is learning the fact this
        # chapter (source_type in {dialogue,observation,witnessed,told}), allow it.
        learning_in_chapter = (k.get("source_type") or "").lower() in {
            "dialogue",
            "observation",
            "witnessed",
            "told",
        } and k.get("learned_this_chapter", True)

        if learning_in_chapter:
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
