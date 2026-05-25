from __future__ import annotations

from typing import Any

from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, vector_literal
from pipeline.retrieval.types import RetrievalQuery, RetrievalResult


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
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
        ORDER BY s.embedding <=> %(qvec)s::vector
        LIMIT %(limit)s
    """


def _event_sql(with_entity_filter: bool) -> str:
    entity_clause = ""
    if with_entity_filter:
        # Match events that overlap entity_ids in any of the involved_* arrays.
        entity_clause = """
          AND (
                e.involved_characters && %(entity_ids)s::uuid[]
             OR e.involved_locations  && %(entity_ids)s::uuid[]
             OR e.involved_objects    && %(entity_ids)s::uuid[]
             OR e.involved_factions   && %(entity_ids)s::uuid[]
          )
        """
    return f"""
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
          AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
          {entity_clause}
        ORDER BY e.embedding <=> %(qvec)s::vector
        LIMIT %(limit)s
    """


def _commitment_sql() -> str:
    return """
        SELECT
            cm.id::text AS item_id,
            NULL::text AS chapter_id,
            cm.foreshadow_chapter AS chapter_number,
            COALESCE(cm.foreshadow_text, '') AS snippet,
            (cm.embedding <=> %(qvec)s::vector) AS distance
        FROM commitments cm
        WHERE cm.novel_id = %(novel_id)s
          AND cm.embedding IS NOT NULL
          AND (%(max_chapter)s::int IS NULL OR cm.foreshadow_chapter <= %(max_chapter)s::int)
        ORDER BY cm.embedding <=> %(qvec)s::vector
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
        if kind not in {"chapter", "scene", "event", "commitment"}:
            raise ValueError(f"Unsupported kind for dense search: {kind}")
        vec = query_embedding if query_embedding is not None else self.embed_query(query.text)
        qvec = vector_literal(vec)

        params: dict[str, Any] = {
            "qvec": qvec,
            "novel_id": query.novel_id,
            "max_chapter": query.max_chapter,
            "limit": limit,
        }

        if kind == "chapter":
            sql = _chapter_sql()
        elif kind == "scene":
            sql = _scene_sql()
        elif kind == "event":
            has_entities = bool(query.entity_ids)
            sql = _event_sql(with_entity_filter=has_entities)
            if has_entities:
                params["entity_ids"] = list(query.entity_ids or [])
        else:
            sql = _commitment_sql()

        rows = self.db.fetchall(sql, params, dict_rows=True)
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
        return results
