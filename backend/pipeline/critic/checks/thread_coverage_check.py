"""Plot-thread coverage check.

Did the draft actually advance each plot thread the planner said it would?

Inputs:
    planned_thread_ids: UUIDs of plot_threads listed in the scene plan
    events: events extracted from the draft (with id when persisted)

Evidence of advancement: a thread_events row exists linking one of the
draft's events to the planned thread.
"""

from __future__ import annotations

from pipeline.critic.types import Finding, Severity
from pipeline.db.client import DBClient


def check_thread_coverage(
    db: DBClient,
    planned_thread_ids: list[str],
    events: list[dict],
) -> list[Finding]:
    if not planned_thread_ids:
        return []

    findings: list[Finding] = []
    event_ids = [str(e["id"]) for e in events if e.get("id")]

    if not event_ids:
        # No persisted events in the draft yet — flag every planned thread as uncovered.
        for tid in planned_thread_ids:
            findings.append(
                Finding(
                    check="thread_coverage",
                    severity=Severity.WARN,
                    message=(
                        f"Planned thread {tid} has no draft events linked to it "
                        f"(draft produced no persisted events at all)."
                    ),
                    context={"thread_id": tid},
                )
            )
        return findings

    rows = db.fetchall(
        """
        SELECT DISTINCT thread_id
          FROM thread_events
         WHERE thread_id = ANY(%s::uuid[])
           AND event_id = ANY(%s::uuid[])
        """,
        (planned_thread_ids, event_ids),
        dict_rows=True,
    )
    covered = {str(r["thread_id"]) for r in rows}
    for tid in planned_thread_ids:
        if str(tid) in covered:
            continue
        findings.append(
            Finding(
                check="thread_coverage",
                severity=Severity.WARN,
                message=(
                    f"Planned thread {tid} was not advanced by any event in this "
                    f"chapter draft (no thread_events linkage)."
                ),
                context={"thread_id": tid},
            )
        )
    return findings
