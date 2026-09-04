"""Orchestrator for the 5 typed continuity checks.

Each check is a pure function over (DB snapshot, draft facts). The runner
collects findings into a single CritiqueReport and labels overall pass/fail
based on whether any FAIL severity finding was produced.
"""

from __future__ import annotations

from pipeline.critic.checks import (
    check_commitments,
    check_entity_mentions,
    check_knowledge_state,
    check_location_possession,
    check_thread_coverage,
)
from pipeline.critic.types import CritiqueReport, DraftChapter
from pipeline.db.client import DBClient


class ContinuityCritic:
    """Run all 5 typed checks against a DraftChapter."""

    def __init__(self, db: DBClient) -> None:
        self.db = db

    def critique(self, draft: DraftChapter) -> CritiqueReport:
        report = CritiqueReport(
            novel_id=draft.novel_id,
            chapter_number=draft.chapter_number,
        )

        report.findings.extend(
            check_entity_mentions(
                self.db, draft.novel_id, draft.mentions, draft.chapter_number
            )
        )
        report.findings.extend(
            check_location_possession(
                self.db,
                draft.novel_id,
                draft.chapter_number,
                draft.location_claims,
                draft.possession_claims,
            )
        )
        report.findings.extend(
            check_knowledge_state(
                self.db,
                draft.novel_id,
                draft.chapter_number,
                draft.knowledge_claims,
            )
        )
        report.findings.extend(
            check_commitments(
                self.db,
                draft.novel_id,
                draft.chapter_number,
                draft.planned_commitment_ids,
                draft.events,
            )
        )
        report.findings.extend(
            check_thread_coverage(self.db, draft.planned_thread_ids, draft.events)
        )

        return report
