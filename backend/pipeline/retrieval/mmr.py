from __future__ import annotations

import math
from dataclasses import replace

from pipeline.retrieval.types import RetrievalResult


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    n = min(len(a), len(b))
    for i in range(n):
        av = a[i]
        bv = b[i]
        dot += av * bv
        na += av * av
        nb += bv * bv
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def mmr(
    candidates: list[RetrievalResult],
    item_embeddings: dict[str, list[float]],
    lambda_: float = 0.7,
    top_k: int = 10,
) -> list[RetrievalResult]:
    """Maximal Marginal Relevance diversification.

    At each step, picks the remaining candidate maximizing
        lambda * rel(d) - (1 - lambda) * max(sim(d, d') for d' in selected).

    Relevance is the candidate's incoming `score` -- the fused hybrid signal
    -- max-normalized to [0, 1] so it shares a scale with the
    cosine novelty penalty. Recomputing relevance from the query embedding
    instead would discard the keyword half of the hybrid and collapse the
    ranking to dense-only.

    Embeddings are used solely for the diversity term; a candidate without one
    is treated as fully novel, and its relevance is on the same normalized
    scale as everyone else's.

    The returned results carry the MMR score in `score`, so the emitted order
    matches it; the pre-diversification score is kept in metadata.
    """
    if not candidates:
        return []
    if top_k <= 0:
        return []

    selected: list[RetrievalResult] = []
    remaining: list[RetrievalResult] = list(candidates)

    max_score = max((float(c.score) for c in candidates), default=0.0)
    rel_cache: dict[str, float] = {
        c.item_id: (float(c.score) / max_score if max_score > 0.0 else 0.0)
        for c in candidates
    }

    while remaining and len(selected) < top_k:
        best_idx = -1
        best_score = -math.inf
        for idx, cand in enumerate(remaining):
            rel = rel_cache[cand.item_id]
            cand_emb = item_embeddings.get(cand.item_id)
            max_sim_selected = 0.0
            for sel in selected:
                sel_emb = item_embeddings.get(sel.item_id)
                if cand_emb is None or sel_emb is None:
                    sim = 0.0
                else:
                    sim = _cosine(cand_emb, sel_emb)
                if sim > max_sim_selected:
                    max_sim_selected = sim
            mmr_score = lambda_ * rel - (1.0 - lambda_) * max_sim_selected
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx
        if best_idx < 0:
            break
        chosen = remaining.pop(best_idx)
        meta = dict(chosen.metadata)
        meta["mmr_score"] = best_score
        meta["mmr_relevance"] = rel_cache[chosen.item_id]
        meta["pre_mmr_score"] = chosen.score
        selected.append(replace(chosen, score=best_score, metadata=meta))

    return selected
