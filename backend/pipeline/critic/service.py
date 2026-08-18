"""Run and persist a continuity critique for an already-ingested chapter.

The critic is a *post-ingest* concern, deliberately decoupled from the write
path: ingestion's job is to get the chapter into the memory layer correctly,
and critiquing it is a separate, optional judgement about that chapter's
consistency. Keeping them separate means

  * ingestion never fails or slows because of the critic,
  * the critic can be turned off entirely (``CRITIC_ENABLED=false``) for
    ingest-only deployments, and
  * a chapter can be re-critiqued later — after a rule change, after a
    transient LLM failure, or across a whole back catalogue — without paying
    for re-extraction. ``pipeline.critic.cli`` is that entry point.

``critique_chapter`` reads the chapter's stored text, so it needs nothing from
the ingestion run beyond a committed chapter row.
"""

from __future__ import annotations

import logging
from typing import Any

from pipeline.config import settings
from pipeline.critic.adapter import (
    build_draft_chapter,
    build_draft_from_extraction,
    extract_draft_claims,
)
from pipeline.critic.persist import persist_critique
from pipeline.critic.runner import ContinuityCritic
from pipeline.critic.types import DraftChapter
from pipeline.db.client import DBClient

logger = logging.getLogger(__name__)


def build_draft(
    db: DBClient,
    *,
    novel_id: str,
    chapter_number: int,
    raw_text: str,
    extracted: dict[str, Any] | None = None,
    use_mock_llm: bool | None = None,
) -> DraftChapter:
    """Assemble the DraftChapter the checks run against.

    ``settings.critique_claims == "extract"`` spends one LLM call per chapter
    reading the chapter text. That call is what lets the knowledge and
    location/possession checks fire at all: the extraction passes record what
    characters *learned* and *gained*, never what they act on as already
    known, so reusing them leaves those checks with nothing to disagree with.

    Mock runs always reuse — ``extract_draft_claims`` returns empty claims
    without a real LLM, and an empty draft passes the critic vacuously, which
    is a worse outcome than the cheaper mode.
    """
    mock = settings.use_mock_llm if use_mock_llm is None else use_mock_llm
    if settings.critique_claims == "extract" and not mock:
        try:
            raw_claims = extract_draft_claims(raw_text, use_mock=False)
            return build_draft_chapter(
                db,
                novel_id=novel_id,
                chapter_number=chapter_number,
                text=raw_text,
                raw_claims=raw_claims,
                planned_thread_ids=[],
                planned_commitment_ids=[],
            )
        except Exception:
            logger.warning(
                "draft-claims extraction failed for chapter %s; falling back to "
                "reused extraction claims (knowledge and possession checks will "
                "not fire for this chapter)",
                chapter_number, exc_info=True,
            )
    return build_draft_from_extraction(
        db,
        novel_id=novel_id,
        chapter_number=chapter_number,
        text=raw_text,
        extracted=extracted or {},
    )


def critique_chapter(
    db: DBClient,
    *,
    novel_id: str,
    chapter_number: int,
    chapter_id: str | None = None,
    raw_text: str | None = None,
    extracted: dict[str, Any] | None = None,
    use_mock_llm: bool | None = None,
) -> dict[str, Any] | None:
    """Critique one committed chapter and persist the report.

    Returns the report summary, or None when the chapter has no text to judge.
    Anything already known by the caller (``chapter_id``, ``raw_text``,
    ``extracted``) can be passed to avoid re-reading it; the CLI passes none of
    them and loads everything from the database.
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

    draft = build_draft(
        db,
        novel_id=novel_id,
        chapter_number=chapter_number,
        raw_text=raw_text,
        extracted=extracted,
        use_mock_llm=use_mock_llm,
    )
    report = ContinuityCritic(db).critique(draft)
    persist_critique(db, chapter_id=chapter_id, report=report)
    return report.summary()


__all__ = ["build_draft", "critique_chapter"]
