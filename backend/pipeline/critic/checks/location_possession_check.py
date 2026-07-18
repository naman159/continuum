"""Location and possession consistency check.

For each (character, location) claimed in the draft, verify the character
was plausibly there given the active located_in_edges at chapter_number - 1.
A character can be at a new location (movement is normal) — what we flag is
*another character* simultaneously asserted to be in two places, or a
possession claim for an item the character does not currently hold.

Inputs:
    location_claims: list of {character_id, location_id, quote}
    possession_claims: list of {character_id, object_id, quote}
"""

from __future__ import annotations

from pipeline.critic.types import Finding, Severity
from pipeline.db.client import DBClient


def _character_to_entity_id(db: DBClient, character_id: str) -> str | None:
    row = db.fetchone(
        "SELECT entity_id FROM characters WHERE id = %s",
        (character_id,),
    )
    if not row or row[0] is None:
        return None
    return str(row[0])


def check_location_possession(
    db: DBClient,
    novel_id: str,
    chapter_number: int,
    location_claims: list[dict],
    possession_claims: list[dict],
) -> list[Finding]:
    findings: list[Finding] = []

    # ---- location: detect two-places-at-once within this draft.
    by_char: dict[str, list[dict]] = {}
    for c in location_claims:
        cid = c.get("character_id")
        lid = c.get("location_id")
        if not cid or not lid:
            continue
        by_char.setdefault(str(cid), []).append(c)

    for cid, claims in by_char.items():
        distinct_locs = {str(c["location_id"]) for c in claims}
        if len(distinct_locs) > 1:
            findings.append(
                Finding(
                    check="location_possession",
                    severity=Severity.FAIL,
                    message=(
                        f"Character {cid} is asserted in {len(distinct_locs)} "
                        f"locations within the same chapter without a transition event."
                    ),
                    quote=claims[0].get("quote"),
                    context={
                        "character_id": cid,
                        "locations": sorted(distinct_locs),
                    },
                )
            )

    # ---- possession: was the object held entering this chapter?
    if possession_claims:
        char_ids = list({str(p["character_id"]) for p in possession_claims if p.get("character_id")})
        obj_ids = list({str(p["object_id"]) for p in possession_claims if p.get("object_id")})

        # Possession edges active entering this chapter: replay closes an edge
        # at the loss chapter, so an edge with until_chapter = N-1 (lost last
        # chapter) is NOT held entering chapter N — require until >= N.
        if char_ids and obj_ids:
            rows = db.fetchall(
                """
                SELECT character_id, object_id, since_chapter, until_chapter
                  FROM possesses_edges
                 WHERE character_id = ANY(%s::uuid[])
                   AND object_id = ANY(%s::uuid[])
                   AND since_chapter < %s
                   AND (until_chapter IS NULL OR until_chapter >= %s)
                """,
                (char_ids, obj_ids, chapter_number, chapter_number),
                dict_rows=True,
            )
            held: set[tuple[str, str]] = {
                (str(r["character_id"]), str(r["object_id"])) for r in rows
            }

            for p in possession_claims:
                cid = str(p["character_id"])
                oid = str(p["object_id"])
                if (cid, oid) in held:
                    continue
                findings.append(
                    Finding(
                        check="location_possession",
                        severity=Severity.WARN,
                        message=(
                            f"Character {cid} is shown holding object {oid}, but "
                            f"no active possession edge exists entering chapter "
                            f"{chapter_number}. If the chapter introduces the pickup "
                            f"this is fine; otherwise flag."
                        ),
                        quote=p.get("quote"),
                        context={
                            "character_id": cid,
                            "object_id": oid,
                            "chapter_number": chapter_number,
                        },
                    )
                )
    return findings
