from __future__ import annotations

from typing import Any

from pipeline.db.client import DBClient
from pipeline.retrieval.types import RetrievalQuery, RetrievalResult


_KIND_SQL: dict[str, str] = {
    "chapter": """
        SELECT
            c.id::text AS item_id,
            c.id::text AS chapter_id,
            c.number AS chapter_number,
            COALESCE(c.summary_short, c.summary, c.title, '') AS snippet,
            ts_rank_cd(c.search_tsv, query) AS raw_score
        FROM chapters c, plainto_tsquery('english', %(q)s) query
        WHERE c.novel_id = %(novel_id)s
          AND c.search_tsv @@ query
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
        ORDER BY raw_score DESC
        LIMIT %(limit)s
    """,
    "scene": """
        SELECT
            s.id::text AS item_id,
            s.chapter_id::text AS chapter_id,
            c.number AS chapter_number,
            COALESCE(s.summary, '') AS snippet,
            ts_rank_cd(s.search_tsv, query) AS raw_score
        FROM scenes s
        JOIN chapters c ON c.id = s.chapter_id,
             plainto_tsquery('english', %(q)s) query
        WHERE c.novel_id = %(novel_id)s
          AND s.search_tsv @@ query
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
        ORDER BY raw_score DESC
        LIMIT %(limit)s
    """,
    "event": """
        SELECT
            e.id::text AS item_id,
            e.chapter_id::text AS chapter_id,
            c.number AS chapter_number,
            COALESCE(e.description, '') AS snippet,
            ts_rank_cd(e.search_tsv, query) AS raw_score
        FROM events e
        JOIN chapters c ON c.id = e.chapter_id,
             plainto_tsquery('english', %(q)s) query
        WHERE c.novel_id = %(novel_id)s
          AND e.search_tsv @@ query
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
        ORDER BY raw_score DESC
        LIMIT %(limit)s
    """,
}


class BM25Search:
    """Postgres FTS-backed BM25-like search using ts_rank_cd."""

    def __init__(self, db: DBClient) -> None:
        self.db = db

    def search(
        self, query: RetrievalQuery, kind: str, limit: int = 50
    ) -> list[RetrievalResult]:
        if kind not in _KIND_SQL:
            raise ValueError(f"Unsupported kind for BM25 search: {kind}")
        if not query.text or not query.text.strip():
            return []

        params: dict[str, Any] = {
            "q": query.text,
            "novel_id": query.novel_id,
            "max_chapter": query.max_chapter,
            "limit": limit,
        }
        rows = self.db.fetchall(_KIND_SQL[kind], params, dict_rows=True)
        if not rows:
            return []

        max_score = max((float(r["raw_score"]) for r in rows), default=0.0)
        results: list[RetrievalResult] = []
        for row in rows:
            raw = float(row["raw_score"])
            normalized = (raw / max_score) if max_score > 0 else 0.0
            results.append(
                RetrievalResult(
                    item_id=row["item_id"],
                    kind=kind,  # type: ignore[arg-type]
                    score=normalized,
                    snippet=row["snippet"] or "",
                    chapter_id=row.get("chapter_id"),
                    chapter_number=row.get("chapter_number"),
                    metadata={"raw_bm25": raw, "source": "bm25"},
                )
            )
        return results
