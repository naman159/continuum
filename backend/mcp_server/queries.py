"""Composite, cutoff-aware lookups for the MCP writer tools.

Query functions moved from the deleted cli/ package plus MCP-specific glue.
`up_to_chapter` is always an INCLUSIVE cap; server.py converts the agent-facing
`writing_chapter` to `writing_chapter - 1` before calling in here.
"""

from __future__ import annotations

from typing import Any

from pipeline.config import settings
from pipeline.critic.adapter import build_draft_chapter, extract_draft_claims
from pipeline.critic.runner import ContinuityCritic
from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService
from pipeline.pipeline import analyze_chapter
from pipeline.retrieval.hybrid import HybridRetriever
from pipeline.retrieval.types import RetrievalQuery


def search_story(
    novel_id: str,
    query_text: str,
    writing_chapter: int,
    *,
    k: int = 8,
    retriever: HybridRetriever | None = None,
) -> dict[str, Any]:
    """Hybrid semantic+keyword search over chapters <= writing_chapter - 1."""
    owned_db: DBClient | None = None
    if retriever is None:
        owned_db = DBClient()
        retriever = HybridRetriever(
            owned_db, EmbeddingService(use_mock=settings.use_mock_llm)
        )
    try:
        bundle = retriever.retrieve(
            RetrievalQuery(
                text=query_text,
                novel_id=novel_id,
                max_chapter=writing_chapter - 1,
                k=k,
            ),
            use_rerank=False,
        )
        return {
            "results": [
                {
                    "kind": str(r.kind),
                    "chapter_number": r.chapter_number,
                    "score": r.score,
                    "snippet": r.snippet,
                }
                for r in bundle.results
            ]
        }
    finally:
        if owned_db is not None:
            owned_db.close()


def _finding_dict(finding: Any) -> dict[str, Any]:
    return {
        "check": finding.check,
        "message": finding.message,
        "quote": finding.quote,
        "suggested_fix": finding.suggested_fix,
    }


def check_continuity(
    novel_id: str,
    chapter_number: int,
    draft_text: str,
    *,
    db: DBClient | None = None,
    use_mock: bool | None = None,
) -> dict[str, Any]:
    """Run claim extraction + the ContinuityCritic on a draft (not saved)."""
    owned = db is None
    client = db if db is not None else DBClient()
    try:
        raw_claims = extract_draft_claims(draft_text, use_mock=use_mock)
        draft = build_draft_chapter(
            client,
            novel_id=novel_id,
            chapter_number=chapter_number,
            text=draft_text,
            raw_claims=raw_claims,
            planned_thread_ids=[],
            planned_commitment_ids=[],
        )
        report = ContinuityCritic(client).critique(draft)
        return {
            "passed": report.passed,
            "fails": [_finding_dict(f) for f in report.fails],
            "warns": [_finding_dict(f) for f in report.warns],
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
    """Ingest a finished draft as source='agent'. Never overwrites."""
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
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"ingested": True, "chapter_id": str(outcome.get("chapter_id"))}
