"""Warn when a draft claims possession unsupported by the preceding chapter.

A pickup within the draft may be legitimate, so this produces warnings rather
than blocking findings. Location consistency needs scene/time evidence and is
not inferred merely from a character visiting multiple places in a chapter.
"""

from __future__ import annotations

from pipeline.critic.types import Finding, Severity
from pipeline.db.client import DBClient


def check_possession(
    db: DBClient,
    chapter_number: int,
    possession_claims: list[dict],
) -> list[Finding]:
    findings: list[Finding] = []

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
                        check="possession",
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
