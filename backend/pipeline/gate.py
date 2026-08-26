"""The agent write gate.

Agent-authored drafts are critiqued *before* extraction: a FAIL, a critic
exception, or a disabled critic refuses the write. Refusal is a return value,
never an exception — `gate_agent_draft` is the only thing standing between an
agent and canon, so a raise inside it must not be able to look like an error
the caller decides to ignore.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from pipeline.config import settings
from pipeline.critic.adapter import build_draft_chapter, extract_draft_claims
from pipeline.critic.runner import ContinuityCritic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    submission_id: str | None = None
    fails: list[dict[str, Any]] | None = None
    warns: list[dict[str, Any]] | None = None
    reason: str | None = None  # 'fail' | 'critic_error' | 'critic_disabled'


def _finding_dict(finding: Any) -> dict[str, Any]:
    return {
        "check": finding.check,
        "severity": str(finding.severity.value),
        "message": finding.message,
        "quote": finding.quote,
        "suggested_fix": finding.suggested_fix,
        "context": finding.context,
    }


def _supersede_pending(
    cur: Any, *, novel_id: str, chapter_number: int, note: str
) -> None:
    """Mark any pending row for this chapter 'rejected'. Takes a cursor (not a
    DBClient) so the caller can run this inside its own transaction."""
    cur.execute(
        """
        UPDATE draft_submissions
           SET status = 'rejected',
               resolved_at = now(),
               resolution_note = %s
         WHERE novel_id = %s AND chapter_number = %s AND status = 'pending'
        """,
        (note, str(novel_id), chapter_number),
    )


def _park(
    db: Any,
    *,
    novel_id: str,
    chapter_number: int,
    title: str | None,
    raw_text: str,
    findings: dict[str, Any],
) -> str:
    """Supersede any pending row for this chapter, then park a new one.

    Both statements run in ONE transaction: superseding the old row without
    parking the new one would drop the author's draft on the floor.

    Note `DBClient.fetchval` defaults to commit=False and ROLLS BACK, so an
    `INSERT ... RETURNING` through it silently discards the row. `transaction()`
    commits on success, which is why the insert goes through its cursor.

    Not wrapped in try/except: refusing an agent write without recording the
    draft would lose the author's work, so a failed park is a hard error.
    """
    with db.transaction() as cur:
        _supersede_pending(
            cur,
            novel_id=novel_id,
            chapter_number=chapter_number,
            note="superseded by resubmission",
        )
        cur.execute(
            """
            INSERT INTO draft_submissions
                (novel_id, chapter_number, title, raw_text, status, findings)
            VALUES (%s, %s, %s, %s, 'pending', %s::jsonb)
            RETURNING id
            """,
            (str(novel_id), chapter_number, title, raw_text, json.dumps(findings)),
        )
        return str(cur.fetchone()[0])


def _supersede_on_pass(db: Any, *, novel_id: str, chapter_number: int) -> None:
    """A passing resubmission still has to clear the stale pending row left by
    an earlier failing draft for the same chapter — otherwise the queue holds
    a 'pending' row for text that the agent has since fixed, and a reviewer
    who opens it hits the duplicate-chapter 409 the moment they try to accept.
    No new row is inserted on this path; there is nothing to park."""
    with db.transaction() as cur:
        _supersede_pending(
            cur,
            novel_id=novel_id,
            chapter_number=chapter_number,
            note="superseded by a passing resubmission",
        )


def gate_agent_draft(
    db: Any,
    *,
    novel_id: str,
    chapter_number: int,
    title: str | None,
    raw_text: str,
    use_mock_llm: bool | None,
) -> GateVerdict:
    """Critique an unsaved agent draft. Park it unless it passes cleanly."""
    if not settings.critic_enabled:
        # A disabled critic is an outage, not a free pass. Nothing is parked:
        # the agent still holds the text and can resubmit once it is back.
        logger.error(
            "refusing agent write for novel %s ch %s: critic is disabled",
            novel_id, chapter_number,
        )
        return GateVerdict(
            passed=False, fails=[], warns=[], reason="critic_disabled"
        )

    try:
        raw_claims = extract_draft_claims(raw_text, use_mock=use_mock_llm)
        draft = build_draft_chapter(
            db,
            novel_id=str(novel_id),
            chapter_number=chapter_number,
            text=raw_text,
            raw_claims=raw_claims,
            planned_thread_ids=[],
            planned_commitment_ids=[],
        )
        report = ContinuityCritic(db).critique(draft)
        # Converting the report is part of what can go wrong with the critic:
        # a report that can't be turned into findings is a critic error, not
        # a clean pass, so this stays inside the try alongside the critique
        # call itself.
        fails = [_finding_dict(f) for f in report.fails]
        warns = [_finding_dict(f) for f in report.warns]
        passed = report.passed
    except Exception as exc:
        logger.exception(
            "critic errored on agent draft for novel %s ch %s; parking",
            novel_id, chapter_number,
        )
        submission_id = _park(
            db,
            novel_id=novel_id,
            chapter_number=chapter_number,
            title=title,
            raw_text=raw_text,
            findings={"fails": [], "warns": [], "error": f"{type(exc).__name__}: {exc}"},
        )
        return GateVerdict(
            passed=False, submission_id=submission_id, fails=[], warns=[],
            reason="critic_error",
        )

    if passed:
        _supersede_on_pass(db, novel_id=novel_id, chapter_number=chapter_number)
        return GateVerdict(passed=True, fails=fails, warns=warns)

    submission_id = _park(
        db,
        novel_id=novel_id,
        chapter_number=chapter_number,
        title=title,
        raw_text=raw_text,
        findings={"fails": fails, "warns": warns},
    )
    return GateVerdict(
        passed=False, submission_id=submission_id, fails=fails, warns=warns,
        reason="fail",
    )
