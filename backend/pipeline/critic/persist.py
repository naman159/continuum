"""Persist a CritiqueReport: one report row per chapter, findings replaced."""

from __future__ import annotations

import json
from typing import Any

from pipeline.critic.types import CritiqueReport


def persist_critique(db: Any, *, chapter_id: str, report: CritiqueReport) -> str:
    with db.transaction() as cur:
        cur.execute("DELETE FROM critique_reports WHERE chapter_id = %s", (chapter_id,))
        cur.execute(
            """
            INSERT INTO critique_reports (chapter_id, passed, stats)
            VALUES (%s, %s, %s::jsonb) RETURNING id
            """,
            (chapter_id, report.passed, json.dumps(report.summary())),
        )
        report_id = str(cur.fetchone()[0])
        for finding in report.findings:
            cur.execute(
                """
                INSERT INTO critique_findings (
                    report_id, check_name, severity, message, quote, evidence
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    report_id,
                    finding.check,
                    finding.severity.value.lower(),
                    finding.message,
                    finding.quote,
                    json.dumps(finding.context, default=str),
                ),
            )
    return report_id


__all__ = ["persist_critique"]
