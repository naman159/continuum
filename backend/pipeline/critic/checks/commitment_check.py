"""Commitment (CFPG foreshadow/payoff) check.

Verifies:
  - All planned foreshadows were actually planted in the draft.
  - All planned payoffs were actually delivered.
  - No prior 'pending' commitment got *accidentally* satisfied — i.e. an
    event that strongly looks like a payoff for a different commitment.
    Approximate via simple text containment for now; embedding-based
    matching is a future improvement.

Inputs:
    planned_commitment_ids: list of commitment UUIDs the caller declared
    events: list of events extracted from the draft
"""

from __future__ import annotations

from pipeline.critic.types import Finding, Severity, words_overlap
from pipeline.critic.types import normalize_text as _normalize
from pipeline.db.client import DBClient


def check_commitments(
    db: DBClient,
    novel_id: str,
    chapter_number: int,
    planned_commitment_ids: list[str],
    events: list[dict],
) -> list[Finding]:
    findings: list[Finding] = []

    # ---- planned commitments — did they all get touched?
    if planned_commitment_ids:
        rows = db.fetchall(
            """
            SELECT id, foreshadow_text, payoff_chapter, status
              FROM commitments
             WHERE id = ANY(%s::uuid[])
            """,
            (planned_commitment_ids,),
            dict_rows=True,
        )
        # In an ideal pipeline the persistence step (after extraction on the draft)
        # updates the commitment row directly. Here we just verify the row's state
        # is consistent with the chapter being drafted.
        for r in rows:
            cid = str(r["id"])
            status = r["status"]
            if status == "pending" and r.get("payoff_chapter") is None:
                findings.append(
                    Finding(
                        check="commitments",
                        severity=Severity.WARN,
                        message=(
                            f"Planned commitment {cid} is still pending after this "
                            f"chapter draft. If the plan said this chapter would "
                            f"advance/satisfy it, the draft failed to do so."
                        ),
                        context={"commitment_id": cid, "current_status": status},
                    )
                )

    # ---- accidental payoff — does any draft event look like a payoff for a
    # pending commitment that was NOT in the plan?
    event_texts = [_normalize(e.get("description", "")) for e in events]
    if event_texts:
        pending = db.fetchall(
            """
            SELECT id, foreshadow_text
              FROM commitments
             WHERE novel_id = %s
               AND status = 'pending'
            """,
            (novel_id,),
            dict_rows=True,
        )
        planned_set = {str(p) for p in (planned_commitment_ids or [])}
        for c in pending:
            cid = str(c["id"])
            if cid in planned_set:
                continue
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
                                f"Draft event may accidentally pay off pending "
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
