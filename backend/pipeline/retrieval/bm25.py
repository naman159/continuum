from __future__ import annotations

from typing import Any

from pipeline.db.client import DBClient
from pipeline.retrieval.types import RetrievalQuery, RetrievalResult


# websearch_to_tsquery sanitizes arbitrary user input, but it ANDs every term,
# so a document missing a single word of the query drops out entirely -- which
# zeroes out recall for natural-language questions. Relaxing '&' to '|' keeps
# partial matches in the candidate pool; ts_rank_cd then ranks documents that
# match more of the query above those that match less. Negated terms
# ("-foo" -> "!foo") keep AND semantics, since OR-ing a negation would match
# nearly every row.
_TSQUERY_CTE = """
    WITH tq AS (
        SELECT CASE
                 WHEN strpos(t::text, '!') > 0 THEN t
                 ELSE replace(t::text, '&', '|')::tsquery
               END AS query
        FROM websearch_to_tsquery('english', %(q)s) AS t
    )
"""

_KIND_SQL: dict[str, str] = {
    "chapter": _TSQUERY_CTE
    + """
        SELECT
            c.id::text AS item_id,
            c.id::text AS chapter_id,
            c.number AS chapter_number,
            COALESCE(c.summary_short, c.summary, c.title, '') AS snippet,
            ts_rank_cd(c.search_tsv, tq.query) AS raw_score
        FROM chapters c, tq
        WHERE c.novel_id = %(novel_id)s
          AND c.search_tsv @@ tq.query
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
        -- item_id breaks ties deterministically: ts_rank_cd produces many
        -- exactly-equal scores, and RRF consumes rank alone, so an arbitrary
        -- tie order propagates straight into the fused ranking and makes the
        -- retrieval eval flaky with no seed to reproduce a failure.
        ORDER BY raw_score DESC, item_id
        LIMIT %(limit)s
    """,
    "scene": _TSQUERY_CTE
    + """
        SELECT
            s.id::text AS item_id,
            s.chapter_id::text AS chapter_id,
            c.number AS chapter_number,
            COALESCE(s.summary, '') AS snippet,
            ts_rank_cd(s.search_tsv, tq.query) AS raw_score
        FROM scenes s
        JOIN chapters c ON c.id = s.chapter_id,
             tq
        WHERE c.novel_id = %(novel_id)s
          AND s.search_tsv @@ tq.query
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
        -- item_id breaks ties deterministically: ts_rank_cd produces many
        -- exactly-equal scores, and RRF consumes rank alone, so an arbitrary
        -- tie order propagates straight into the fused ranking and makes the
        -- retrieval eval flaky with no seed to reproduce a failure.
        ORDER BY raw_score DESC, item_id
        LIMIT %(limit)s
    """,
    "event": _TSQUERY_CTE
    + """
        SELECT
            e.id::text AS item_id,
            e.chapter_id::text AS chapter_id,
            c.number AS chapter_number,
            COALESCE(e.description, '') AS snippet,
            ts_rank_cd(e.search_tsv, tq.query) AS raw_score
        FROM events e
        JOIN chapters c ON c.id = e.chapter_id,
             tq
        WHERE c.novel_id = %(novel_id)s
          AND e.search_tsv @@ tq.query
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
        -- item_id breaks ties deterministically: ts_rank_cd produces many
        -- exactly-equal scores, and RRF consumes rank alone, so an arbitrary
        -- tie order propagates straight into the fused ranking and makes the
        -- retrieval eval flaky with no seed to reproduce a failure.
        ORDER BY raw_score DESC, item_id
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
