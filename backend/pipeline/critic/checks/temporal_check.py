"""Temporal-consistency check.

Walk the temporal_constraints graph for events emitted by the draft plus
prior events in the novel; surface impossible orderings.

The check is intentionally conservative — it only flags direct cycles and
"before" / "after" contradictions where both sides have a story_time_ordinal
that violates the constraint. A real SAT solver could go further; that is a
future enhancement.

Inputs:
    events: [{id?, description, story_time_ordinal?, quote?}]
"""

from __future__ import annotations

from pipeline.critic.types import Finding, Severity
from pipeline.db.client import DBClient


def check_temporal_consistency(
    db: DBClient,
    novel_id: str,
    events: list[dict],
) -> list[Finding]:
    if not events:
        return []

    event_ids = [str(e["id"]) for e in events if e.get("id")]
    if not event_ids:
        return []

    rows = db.fetchall(
        """
        SELECT tc.event_a_id, tc.event_b_id, tc.relation,
               ea.story_time_ordinal AS a_time,
               eb.story_time_ordinal AS b_time
          FROM temporal_constraints tc
          JOIN events ea ON ea.id = tc.event_a_id
          JOIN events eb ON eb.id = tc.event_b_id
         WHERE tc.event_a_id = ANY(%s::uuid[])
            OR tc.event_b_id = ANY(%s::uuid[])
        """,
        (event_ids, event_ids),
        dict_rows=True,
    )

    findings: list[Finding] = []
    seen_pairs: set[tuple[str, str]] = set()
    for r in rows:
        a = str(r["event_a_id"])
        b = str(r["event_b_id"])
        rel = r["relation"]
        a_time = r["a_time"]
        b_time = r["b_time"]
        pair = (a, b, rel)
        # dedupe
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        if a_time is None or b_time is None:
            continue

        violation = False
        if rel == "before" and not (a_time < b_time):
            violation = True
        elif rel == "after" and not (a_time > b_time):
            violation = True
        elif rel == "simultaneous" and a_time != b_time:
            violation = True
        if violation:
            findings.append(
                Finding(
                    check="temporal_consistency",
                    severity=Severity.FAIL,
                    message=(
                        f"Temporal constraint violated: event {a} "
                        f"({rel}) event {b}, but story_time_ordinals are "
                        f"{a_time} and {b_time}."
                    ),
                    context={
                        "event_a_id": a,
                        "event_b_id": b,
                        "relation": rel,
                        "a_time": a_time,
                        "b_time": b_time,
                    },
                )
            )

    return findings
