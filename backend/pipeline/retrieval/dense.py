from __future__ import annotations

import logging
from typing import Any

from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, vector_literal
from pipeline.retrieval.types import RetrievalQuery, RetrievalResult

logger = logging.getLogger(__name__)

_TABLE_FOR_KIND = {"chapter": "chapters", "scene": "scenes", "event": "events"}


def _chapter_sql() -> str:
    return """
        SELECT
            c.id::text AS item_id,
            c.id::text AS chapter_id,
            c.number AS chapter_number,
            COALESCE(c.summary_short, c.summary, c.title, '') AS snippet,
            (c.embedding <=> %(qvec)s::vector) AS distance
        FROM chapters c
        WHERE c.novel_id = %(novel_id)s
          AND c.embedding IS NOT NULL
          AND c.embedding_model = %(embedding_model)s
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
        ORDER BY c.embedding <=> %(qvec)s::vector
        LIMIT %(limit)s
    """


def _scene_sql() -> str:
    return """
        SELECT
            s.id::text AS item_id,
            s.chapter_id::text AS chapter_id,
            c.number AS chapter_number,
            COALESCE(s.summary, '') AS snippet,
            (s.embedding <=> %(qvec)s::vector) AS distance
        FROM scenes s
        JOIN chapters c ON c.id = s.chapter_id
        WHERE c.novel_id = %(novel_id)s
          AND s.embedding IS NOT NULL
          AND s.embedding_model = %(embedding_model)s
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
        ORDER BY s.embedding <=> %(qvec)s::vector
        LIMIT %(limit)s
    """


def _event_sql() -> str:
    return """
        SELECT
            e.id::text AS item_id,
            e.chapter_id::text AS chapter_id,
            c.number AS chapter_number,
            COALESCE(e.description, '') AS snippet,
            (e.embedding <=> %(qvec)s::vector) AS distance
        FROM events e
        JOIN chapters c ON c.id = e.chapter_id
        WHERE c.novel_id = %(novel_id)s
          AND e.embedding IS NOT NULL
          AND e.embedding_model = %(embedding_model)s
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
        ORDER BY e.embedding <=> %(qvec)s::vector
        LIMIT %(limit)s
    """


class DenseSearch:
    """pgvector-based dense semantic search using cosine distance."""

    def __init__(self, db: DBClient, embedder: EmbeddingService) -> None:
        self.db = db
        self.embedder = embedder

    def embed_query(self, text: str) -> list[float]:
        return self.embedder.embed_text(text)

    def search(
        self,
        query: RetrievalQuery,
        kind: str,
        limit: int = 50,
        *,
        query_embedding: list[float] | None = None,
    ) -> list[RetrievalResult]:
        if kind not in _TABLE_FOR_KIND:
            raise ValueError(f"Unsupported kind for dense search: {kind}")
        vec = query_embedding if query_embedding is not None else self.embed_query(query.text)
        qvec = vector_literal(vec)

        # Vectors are only comparable to vectors from the same model. Mixed
        # stores are the normal case, not an exotic one -- you develop against
        # USE_MOCK_LLM and then switch to a real model -- and a hash vector is
        # indistinguishable from a real one once written. embeddings.py records
        # provenance on every write precisely so this query can exclude the
        # ones that would otherwise return as confident nonsense.
        params: dict[str, Any] = {
            "qvec": qvec,
            "novel_id": query.novel_id,
            "max_chapter": query.max_chapter,
            "limit": limit,
            "embedding_model": self.embedder.model_name,
        }

        if kind == "chapter":
            sql = _chapter_sql()
        elif kind == "scene":
            sql = _scene_sql()
        else:
            sql = _event_sql()

        rows = self.db.fetchall(sql, params, dict_rows=True)
        if not rows:
            self._warn_if_provenance_excluded_everything(kind, query.novel_id)
        results: list[RetrievalResult] = []
        for row in rows:
            distance = float(row["distance"])
            similarity = 1.0 - distance
            # Cosine distance can be in [0, 2] for non-normalized vectors;
            # clamp similarity to [0, 1] for downstream blending.
            if similarity < 0.0:
                similarity = 0.0
            if similarity > 1.0:
                similarity = 1.0
            results.append(
                RetrievalResult(
                    item_id=row["item_id"],
                    kind=kind,  # type: ignore[arg-type]
                    score=similarity,
                    snippet=row["snippet"] or "",
                    chapter_id=row.get("chapter_id"),
                    chapter_number=row.get("chapter_number"),
                    metadata={"cosine_distance": distance, "source": "dense"},
                )
            )
        # Break distance ties by item_id, for the same reason bm25.py orders by
        # (raw_score, item_id): RRF consumes rank alone, so an arbitrary tie
        # order propagates straight into the fused ranking and makes the
        # retrieval eval flaky with no seed to reproduce a failure. Sorted here
        # rather than in the ORDER BY because appending a second key there
        # would stop the query being a pure HNSW index scan.
        results.sort(key=lambda r: (r.metadata["cosine_distance"], r.item_id))
        return results

    def _warn_if_provenance_excluded_everything(self, kind: str, novel_id: str) -> None:
        """Say so when the provenance filter is why nothing came back.

        Without this, pointing EMBEDDING_MODEL at a model the store was not
        embedded with turns dense retrieval into a silent no-op: the request
        still succeeds, BM25 still returns rows, and the only symptom is
        quietly worse results.
        """
        table = _TABLE_FOR_KIND.get(kind)
        if table is None:
            return
        try:
            other = self.db.fetchval(
                f"""
                SELECT string_agg(DISTINCT COALESCE(embedding_model, '(null)'), ', ')
                  FROM {table}
                 WHERE embedding IS NOT NULL AND embedding_model IS DISTINCT FROM %s
                """,
                (self.embedder.model_name,),
            )
        except Exception:  # diagnostics must never break a search
            return
        if other:
            logger.warning(
                "dense search for %s returned nothing: store holds vectors from %s "
                "but the active embedder is %r. Re-embed, or set EMBEDDING_MODEL back.",
                kind, other, self.embedder.model_name,
            )
