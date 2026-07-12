"""reads.search: cutoff-aware hybrid (semantic + keyword) search.

Adapted from the deleted `mcp_server/queries.py::search_story`. Retriever
construction is behind `_build_retriever` so tests can inject a fake without
touching embeddings or Postgres.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pipeline.config import settings
from pipeline.embeddings import EmbeddingService
from pipeline.retrieval.hybrid import HybridRetriever
from pipeline.retrieval.types import RetrievalQuery
from reads.common import resolve_cutoff


def _build_retriever(db: Any) -> HybridRetriever:
    return HybridRetriever(db, EmbeddingService(use_mock=settings.use_mock_llm))


def search(
    db: Any,
    novel_id: UUID | str,
    query_text: str,
    up_to_chapter: int | None,
    k: int = 8,
    retriever: Any | None = None,
) -> dict[str, Any]:
    """Hybrid semantic+keyword search over chapters <= the resolved cutoff."""
    max_chapter = resolve_cutoff(db, novel_id, up_to_chapter)
    active_retriever = retriever if retriever is not None else _build_retriever(db)
    bundle = active_retriever.retrieve(
        RetrievalQuery(
            text=query_text,
            novel_id=str(novel_id),
            max_chapter=max_chapter,
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
