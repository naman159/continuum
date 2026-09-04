"""Write-spine glue for the MCP writer tools: check_continuity + save_chapter.

All read lookups moved to the shared `reads/` layer (see reads/*); what remains
here is the agent-facing surface over `critique_draft` and `analyze_chapter`.

This is where the hard "no" lives. The critique itself is caller-agnostic — the
same checks run for the Process page and the CLI — but an agent writes to canon
unattended, so this surface (and only this surface) asks for
`on_continuity_fail="block"`. Being the MCP tool is what makes a caller an
agent; it is not a string anyone can pass.
"""

from __future__ import annotations

from typing import Any

from pipeline.config import settings
from pipeline.critic.service import critique_draft
from pipeline.db.client import DBClient
from pipeline.pipeline import analyze_chapter


def _brief(finding: dict[str, Any]) -> dict[str, Any]:
    """Trim a finding to what an agent needs to revise. `severity` and
    `context` are dropped: check/message/quote/suggested_fix is the actionable
    part, and the caller already knows FAILs from WARNs by which list it is in.
    """
    return {
        "check": finding.get("check"),
        "message": finding.get("message"),
        "quote": finding.get("quote"),
        "suggested_fix": finding.get("suggested_fix"),
    }


def check_continuity(
    novel_id: str,
    chapter_number: int,
    draft_text: str,
    *,
    db: DBClient | None = None,
    use_mock: bool | None = None,
) -> dict[str, Any]:
    """Critique a draft without saving it — the same run `save_chapter` does.

    Identical checks, identical verdict; the only difference is that nothing is
    written either way. `status` distinguishes a real verdict from an outage,
    which `passed` alone cannot: an unavailable critic is not a pass.
    """
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        critique = critique_draft(
            client,
            novel_id=novel_id,
            chapter_number=chapter_number,
            text=draft_text,
            use_mock_llm=use_mock,
        )
        return {
            "passed": critique.passed,
            "status": critique.status,
            "error": critique.error,
            "fails": [_brief(f) for f in critique.fails],
            "warns": [_brief(f) for f in critique.warns],
        }
    finally:
        if owned:
            client.close()


def save_chapter(
    novel_id: str,
    chapter_number: int,
    text: str,
    title: str | None = None,
    *,
    db: DBClient | None = None,
) -> dict[str, Any]:
    """Ingest a finished draft as source='agent'. Never overwrites.

    Asks for `on_continuity_fail="block"`: a FAIL refuses the write and parks
    the draft rather than recording a finding and ingesting anyway.
    """
    if not text or not text.strip():
        return {"error": "text must not be empty"}
    try:
        outcome = analyze_chapter(
            novel_id=novel_id,
            chapter_number=chapter_number,
            raw_text=text,
            chapter_title=title,
            use_mock_llm=None,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            db=db,
            replace=False,
            source="agent",
            on_continuity_fail="block",
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    # A refusal is a normal outcome, not an error: pass it through verbatim so
    # the agent gets the findings and can revise. The presence of an "ingested"
    # key means analyze_chapter refused before writing.
    if "ingested" in outcome:
        return outcome
    # materialized/critique are the caller's only signal that the best-effort
    # MATERIALIZE/CRITIQUE phases failed (see analyze_chapter's result dict).
    return {
        "ingested": True,
        "chapter_id": str(outcome.get("chapter_id")),
        "materialized": bool(outcome.get("materialized")),
        "critique": outcome.get("critique"),
    }
