from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, vector_literal
from pipeline.retrieval.bm25 import BM25Search
from pipeline.retrieval.dense import DenseSearch
from pipeline.retrieval.fusion import reciprocal_rank_fusion
from pipeline.retrieval.mmr import mmr
from pipeline.retrieval.types import RetrievalQuery, RetrievalResult

logger = logging.getLogger(__name__)

_KINDS: tuple[str, ...] = ("chapter", "scene", "event")

# How many candidates each stage emits before the next one trims.
_PER_KIND_LIMIT = 50
_FUSION_POOL = 50

_TABLE_FOR_KIND = {"chapter": "chapters", "scene": "scenes", "event": "events"}


class HybridRetriever:
    """BM25 + dense -> RRF -> MMR diversify."""

    def __init__(self, db: DBClient, embedder: EmbeddingService) -> None:
        self.db = db
        self.embedder = embedder
        self.bm25 = BM25Search(db)
        self.dense = DenseSearch(db, embedder)

    def retrieve(
        self,
        query: RetrievalQuery,
        *,
        use_mmr: bool = True,
        mmr_lambda: float = 0.7,
    ) -> list[RetrievalResult]:
        # Embed the query once for both dense search and MMR.
        query_embedding = self.embedder.embed_text(query.text)

        # Stage 1: BM25 + dense in parallel per kind.
        per_kind: dict[tuple[str, str], list[RetrievalResult]] = {}
        with ThreadPoolExecutor(max_workers=2 * len(_KINDS)) as pool:
            futures = {}
            for kind in _KINDS:
                futures[("bm25", kind)] = pool.submit(
                    self.bm25.search, query, kind, _PER_KIND_LIMIT
                )
                futures[("dense", kind)] = pool.submit(
                    self.dense.search,
                    query,
                    kind,
                    _PER_KIND_LIMIT,
                    query_embedding=query_embedding,
                )
            for key, fut in futures.items():
                try:
                    per_kind[key] = fut.result()
                except Exception as exc:
                    # Log rather than raise: one dead stage should degrade the
                    # ranking, not fail the request. Without the log a fully
                    # offline dense stage is invisible -- the request still
                    # returns 200 with BM25-only results.
                    logger.warning(
                        "retrieval stage %s/%s failed, continuing degraded: %s",
                        key[0], key[1], exc,
                    )
                    per_kind[key] = []

        # Stage 2: Reciprocal Rank Fusion across all sources/kinds.
        fused = reciprocal_rank_fusion(
            *per_kind.values(), k=60, limit=_FUSION_POOL
        )

        # Stage 3: MMR diversification.
        if not (use_mmr and fused):
            return fused[: query.k]
        return mmr(
            fused,
            self._fetch_item_embeddings(fused),
            lambda_=mmr_lambda,
            top_k=query.k,
        )

    def _fetch_item_embeddings(
        self, results: list[RetrievalResult]
    ) -> dict[str, list[float]]:
        by_kind: dict[str, list[str]] = {}
        for r in results:
            by_kind.setdefault(r.kind, []).append(r.item_id)

        out: dict[str, list[float]] = {}
        for kind, ids in by_kind.items():
            table = _TABLE_FOR_KIND.get(kind)
            if not table or not ids:
                continue
            rows = self.db.fetchall(
                f"SELECT id::text AS id, embedding FROM {table} WHERE id = ANY(%s::uuid[])",
                (ids,),
                dict_rows=True,
            )
            for row in rows:
                if row.get("embedding"):
                    out[row["id"]] = _parse_vector(row["embedding"])
        return out


def _parse_vector(value: str) -> list[float]:
    """pgvector hands back its text form, e.g. "[0.1,0.2,...]"."""
    body = value.strip().strip("[]")
    return [float(x) for x in body.split(",")] if body else []


__all__ = ["HybridRetriever", "vector_literal"]
