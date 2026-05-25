from __future__ import annotations

from pipeline.retrieval.types import RetrievalResult


def reciprocal_rank_fusion(
    *ranked_lists: list[RetrievalResult],
    k: int = 60,
    limit: int = 50,
) -> list[RetrievalResult]:
    """Combine multiple ranked lists via Reciprocal Rank Fusion.

    Score for a document d: sum over input lists of 1 / (k + rank_in_list(d)),
    where rank is 1-indexed. Items missing from a list contribute nothing
    from that list. Deduplicates by item_id, preferring richer metadata.
    """

    fused_scores: dict[str, float] = {}
    representatives: dict[str, RetrievalResult] = {}
    source_ranks: dict[str, list[int]] = {}

    for ranked in ranked_lists:
        for rank_idx, result in enumerate(ranked, start=1):
            item_id = result.item_id
            fused_scores[item_id] = fused_scores.get(item_id, 0.0) + 1.0 / (k + rank_idx)
            source_ranks.setdefault(item_id, []).append(rank_idx)
            if item_id not in representatives:
                representatives[item_id] = result

    fused: list[RetrievalResult] = []
    for item_id, score in fused_scores.items():
        base = representatives[item_id]
        merged_meta = dict(base.metadata)
        merged_meta["rrf_score"] = score
        merged_meta["rrf_source_ranks"] = list(source_ranks[item_id])
        fused.append(
            RetrievalResult(
                item_id=base.item_id,
                kind=base.kind,
                score=score,
                snippet=base.snippet,
                chapter_id=base.chapter_id,
                chapter_number=base.chapter_number,
                metadata=merged_meta,
            )
        )

    fused.sort(key=lambda r: r.score, reverse=True)
    return fused[:limit]
