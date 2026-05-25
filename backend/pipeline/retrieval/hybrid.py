from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, vector_literal
from pipeline.retrieval.bm25 import BM25Search
from pipeline.retrieval.dense import DenseSearch
from pipeline.retrieval.fusion import reciprocal_rank_fusion
from pipeline.retrieval.mmr import mmr
from pipeline.retrieval.rerank import LLMReranker
from pipeline.retrieval.types import RetrievalBundle, RetrievalQuery, RetrievalResult


_DEFAULT_KINDS: tuple[str, ...] = ("chapter", "scene", "event")

# How many candidates each stage emits before the next one trims.
_PER_KIND_LIMIT = 50
_FUSION_POOL = 50
_RERANK_POOL = 50


def _summarize(results: list[RetrievalResult], n: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "item_id": r.item_id,
            "kind": r.kind,
            "score": r.score,
            "chapter_number": r.chapter_number,
        }
        for r in results[:n]
    ]


class HybridRetriever:
    """Hybrid retriever: BM25 + dense -> RRF -> rerank -> MMR diversify.

    The orchestrator records per-stage timings and top-K snapshots in the
    returned RetrievalBundle's `debug` dict.
    """

    def __init__(
        self,
        db: DBClient,
        embedder: EmbeddingService,
        reranker: LLMReranker | None = None,
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.reranker = reranker
        self.bm25 = BM25Search(db)
        self.dense = DenseSearch(db, embedder)

    def retrieve(
        self,
        query: RetrievalQuery,
        kinds: list[str] | None = None,
        *,
        use_rerank: bool = True,
        use_mmr: bool = True,
        mmr_lambda: float = 0.7,
    ) -> RetrievalBundle:
        active_kinds = tuple(kinds) if kinds else _DEFAULT_KINDS
        debug: dict[str, Any] = {"kinds": list(active_kinds), "timings_ms": {}, "stages": {}}

        # Embed the query once for both dense search and MMR.
        t0 = time.perf_counter()
        query_embedding = self.embedder.embed_text(query.text)
        debug["timings_ms"]["embed_query"] = (time.perf_counter() - t0) * 1000.0

        # Stage 1: BM25 + dense in parallel per kind.
        t0 = time.perf_counter()
        per_kind: dict[str, dict[str, list[RetrievalResult]]] = {}
        with ThreadPoolExecutor(max_workers=max(2, 2 * len(active_kinds))) as pool:
            futures = {}
            for kind in active_kinds:
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
            for (source, kind), fut in futures.items():
                try:
                    res = fut.result()
                except Exception as exc:
                    debug.setdefault("errors", []).append(
                        {"stage": source, "kind": kind, "error": str(exc)}
                    )
                    res = []
                per_kind.setdefault(kind, {})[source] = res
        debug["timings_ms"]["retrieve_parallel"] = (time.perf_counter() - t0) * 1000.0

        debug["stages"]["bm25"] = {
            k: _summarize(v.get("bm25", [])) for k, v in per_kind.items()
        }
        debug["stages"]["dense"] = {
            k: _summarize(v.get("dense", [])) for k, v in per_kind.items()
        }

        # Stage 2: Reciprocal Rank Fusion across all sources/kinds.
        t0 = time.perf_counter()
        ranked_lists: list[list[RetrievalResult]] = []
        for kind in active_kinds:
            ranked_lists.append(per_kind.get(kind, {}).get("bm25", []))
            ranked_lists.append(per_kind.get(kind, {}).get("dense", []))
        fused = reciprocal_rank_fusion(*ranked_lists, k=60, limit=_FUSION_POOL)
        debug["timings_ms"]["rrf"] = (time.perf_counter() - t0) * 1000.0
        debug["stages"]["rrf"] = _summarize(fused, n=_FUSION_POOL)

        # Stage 3: Rerank top _RERANK_POOL with cross-encoder LLM.
        rerank_pool = fused[:_RERANK_POOL]
        if use_rerank and self.reranker is not None and rerank_pool:
            t0 = time.perf_counter()
            reranked = self.reranker.rerank(query, rerank_pool, top_k=_RERANK_POOL)
            debug["timings_ms"]["rerank"] = (time.perf_counter() - t0) * 1000.0
            debug["stages"]["rerank"] = _summarize(reranked, n=_RERANK_POOL)
        else:
            reranked = rerank_pool

        # Stage 4: MMR diversification on the reranked pool.
        if use_mmr and reranked:
            t0 = time.perf_counter()
            item_embeddings = self._fetch_item_embeddings(reranked)
            diversified = mmr(
                query_embedding,
                reranked,
                item_embeddings,
                lambda_=mmr_lambda,
                top_k=query.k,
            )
            debug["timings_ms"]["mmr"] = (time.perf_counter() - t0) * 1000.0
            debug["stages"]["mmr"] = _summarize(diversified, n=query.k)
        else:
            diversified = reranked[: query.k]

        return RetrievalBundle(query=query, results=diversified, debug=debug)

    def _fetch_item_embeddings(
        self, results: list[RetrievalResult]
    ) -> dict[str, list[float]]:
        """Load embeddings for the given results, grouped by kind."""
        by_kind: dict[str, list[str]] = {}
        for r in results:
            by_kind.setdefault(r.kind, []).append(r.item_id)

        out: dict[str, list[float]] = {}
        table_map = {
            "chapter": "chapters",
            "scene": "scenes",
            "event": "events",
            "commitment": "commitments",
        }
        for kind, ids in by_kind.items():
            table = table_map.get(kind)
            if not table or not ids:
                continue
            rows = self.db.fetchall(
                f"SELECT id::text AS id, embedding FROM {table} WHERE id = ANY(%s::uuid[])",
                (ids,),
                dict_rows=True,
            )
            for row in rows:
                emb = row.get("embedding")
                if emb is None:
                    continue
                out[row["id"]] = _coerce_embedding(emb)
        return out


def _coerce_embedding(value: Any) -> list[float]:
    """Convert various pgvector return shapes into list[float]."""
    if value is None:
        return []
    if isinstance(value, list):
        return [float(v) for v in value]
    if isinstance(value, tuple):
        return [float(v) for v in value]
    if isinstance(value, str):
        # pgvector text form like "[0.1,0.2,...]"
        s = value.strip().strip("[]")
        if not s:
            return []
        return [float(x) for x in s.split(",")]
    # numpy array, memoryview, etc.
    try:
        return [float(v) for v in value]
    except Exception:
        return []


__all__ = ["HybridRetriever", "vector_literal"]
