"""The continuity critique: one entry point, run once per chapter.

`critique_draft` is the only place a draft is judged. It runs *pre-ingest* —
before extraction has written anything — for two reasons:

  * a refusal costs one claims-extraction call instead of 13 extraction passes
    per chunk, and
  * a refused draft never reaches the write tier at all. Refusing after ingest
    would mean rolling back, and `pipeline.ingestion.ingest.delete_chapter_data`
    documents what a rollback cannot undo: entity rows, plot_threads upserts
    and canon_facts updates from the rejected draft survive it. Rejected text
    must not be able to mint entities.

Running pre-ingest also removes a self-comparison hazard: post-ingest, the
chapter's own canon facts are already committed, so a mention would be checked
against the row it just produced.

**Judging and enforcing are separate.** `critique_draft` returns a verdict and
never decides what it means. `analyze_chapter`'s `on_continuity_fail` policy
does that — "block" for callers that must not write on a FAIL (the MCP agent
surface), "warn" for callers where a human is already the review step (the CLI
and the Process page). Both run the same checks once and see the same findings;
only the consequence differs.

`critique_chapter` re-judges an already-ingested chapter from its stored text —
after a rule change, a transient failure, or across a back catalogue.
`pipeline.critic.cli` is that entry point.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pipeline.config import settings
from pipeline.critic.adapter import build_draft_chapter, extract_draft_claims
from pipeline.critic.persist import persist_critique
from pipeline.critic.runner import ContinuityCritic
from pipeline.critic.types import CritiqueReport
from pipeline.db.client import DBClient

logger = logging.getLogger(__name__)

Status = Literal["ok", "unavailable", "error"]


def _finding_dict(finding: Any) -> dict[str, Any]:
    return {
        "check": finding.check,
        "severity": str(finding.severity.value),
        "message": finding.message,
        "quote": finding.quote,
        "suggested_fix": finding.suggested_fix,
        "context": finding.context,
    }


@dataclass(frozen=True)
class DraftCritique:
    """The verdict on one draft.

    `status` separates "the critic judged this" from "the critic never ran",
    which a bare pass/fail boolean cannot express. Only `ok` carries a
    judgement; `unavailable` (critic disabled, or mock mode) and `error` (the
    critique itself blew up) are outages. A blocking caller must refuse on all
    three of not-ok, because an outage is not a clean bill of health.

    `fails`/`warns` are converted eagerly rather than in a property: converting
    a finding is itself something that can fail on a malformed report, and that
    belongs inside `critique_draft`'s error handling, not in a caller's
    attribute access.
    """

    status: Status
    report: CritiqueReport | None = None
    fails: list[dict[str, Any]] = field(default_factory=list)
    warns: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "ok" and not self.fails

    def summary(self) -> dict[str, Any]:
        """One shape for every outcome, so callers never have to branch on
        status just to report what happened."""
        if self.report is not None:
            return {**self.report.summary(), "status": self.status}
        return {"passed": None, "status": self.status, "error": self.error}


def critique_draft(
    db: Any,
    *,
    novel_id: str,
    chapter_number: int,
    text: str,
    use_mock_llm: bool | None = None,
) -> DraftCritique:
    """Judge one draft against committed state. Never raises."""
    if not settings.critic_enabled:
        logger.warning(
            "critique skipped for novel %s ch %s: CRITIC_ENABLED=false",
            novel_id, chapter_number,
        )
        return DraftCritique(
            status="unavailable", error="critic is disabled (CRITIC_ENABLED=false)"
        )

    mock = settings.use_mock_llm if use_mock_llm is None else use_mock_llm
    if mock:
        # `extract_draft_claims` returns empty claim lists without a real LLM,
        # and an empty draft passes every check vacuously. Reporting that as a
        # pass would make USE_MOCK_LLM=true a silent bypass for any caller that
        # blocks on FAIL — the same free pass a disabled critic must not get.
        logger.warning(
            "critique unavailable for novel %s ch %s: claims extraction needs a "
            "real LLM, and an empty mock draft would pass vacuously",
            novel_id, chapter_number,
        )
        return DraftCritique(
            status="unavailable", error="claims extraction needs a real LLM (mock mode)"
        )

    try:
        raw_claims = extract_draft_claims(text, use_mock=False)
        draft = build_draft_chapter(
            db,
            novel_id=str(novel_id),
            chapter_number=chapter_number,
            text=text,
            raw_claims=raw_claims,
            planned_thread_ids=[],
            planned_commitment_ids=[],
        )
        report = ContinuityCritic(db).critique(draft)
        # Conversion stays inside the try: a report that cannot be turned into
        # findings is a critic error, not a clean pass.
        fails = [_finding_dict(f) for f in report.fails]
        warns = [_finding_dict(f) for f in report.warns]
    except Exception as exc:
        logger.exception(
            "critique errored for novel %s ch %s", novel_id, chapter_number
        )
        return DraftCritique(status="error", error=f"{type(exc).__name__}: {exc}")

    return DraftCritique(status="ok", report=report, fails=fails, warns=warns)


def critique_chapter(
    db: DBClient,
    *,
    novel_id: str,
    chapter_number: int,
    chapter_id: str | None = None,
    raw_text: str | None = None,
    use_mock_llm: bool | None = None,
) -> dict[str, Any] | None:
    """Re-critique one committed chapter from its stored text and persist the
    report. Returns the summary, or None when the chapter has no text to judge.

    Only an `ok` verdict is persisted: an outage must not overwrite a chapter's
    existing report with an empty one.
    """
    if chapter_id is None or raw_text is None:
        row = db.fetchone(
            "SELECT id, raw_text FROM chapters WHERE novel_id = %s AND number = %s",
            (novel_id, chapter_number),
            dict_rows=True,
        )
        if row is None:
            raise ValueError(
                f"Chapter {chapter_number} does not exist for novel {novel_id}."
            )
        chapter_id = chapter_id or str(row["id"])
        raw_text = raw_text if raw_text is not None else (row["raw_text"] or "")

    if not (raw_text or "").strip():
        logger.warning(
            "chapter %s of novel %s has no stored text; skipping critique",
            chapter_number, novel_id,
        )
        return None

    critique = critique_draft(
        db,
        novel_id=novel_id,
        chapter_number=chapter_number,
        text=raw_text,
        use_mock_llm=use_mock_llm,
    )
    if critique.report is not None:
        persist_critique(db, chapter_id=chapter_id, report=critique.report)
    return critique.summary()


__all__ = ["DraftCritique", "critique_draft", "critique_chapter"]
