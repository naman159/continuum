"""Warn when a draft event resembles the payoff of a pending commitment.

Word overlap supplies review evidence, not proof that a payoff was intended
or completed. This check does not require a chapter planner.
"""

from __future__ import annotations

from pipeline.critic.types import Finding, Severity, words_overlap
from pipeline.critic.types import normalize_text as _normalize
from pipeline.db.client import DBClient


def check_commitments(
    db: DBClient,
    novel_id: str,
    chapter_number: int,
    events: list[dict],
) -> list[Finding]:
    findings: list[Finding] = []

    event_texts = [_normalize(e.get("description", "")) for e in events]
    if event_texts:
        # foreshadow_chapter <= this chapter: a commitment planted in a LATER
        # chapter does not exist yet in story time, so a draft event cannot
        # pay it off. Without this bound, re-processing an early
        # chapter warns about foreshadows from the end of the book.
        pending = db.fetchall(
            """
            SELECT id, foreshadow_text
              FROM commitments
             WHERE novel_id = %s
               AND status = 'pending'
               AND (foreshadow_chapter IS NULL OR foreshadow_chapter <= %s)
            """,
            (novel_id, chapter_number),
            dict_rows=True,
        )
        for c in pending:
            cid = str(c["id"])
            fore_norm = _normalize(c["foreshadow_text"])
            if not fore_norm:
                continue
            for desc in event_texts:
                if not desc:
                    continue
                overlap = words_overlap(fore_norm, desc, min_words=3, ratio=0.4)
                if overlap:
                    findings.append(
                        Finding(
                            check="commitments",
                            severity=Severity.WARN,
                            message=(
                                f"Draft event may pay off pending "
                                f"commitment {cid} (foreshadow: "
                                f"'{c['foreshadow_text'][:80]}...')."
                            ),
                            quote=desc[:200],
                            context={
                                "commitment_id": cid,
                                "overlap_terms": sorted(overlap),
                            },
                        )
                    )
                    break

    return findings
