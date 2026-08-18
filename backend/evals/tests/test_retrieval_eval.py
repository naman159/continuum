from __future__ import annotations

import os

import pytest

from evals.harness import ingest_fixture, run_retrieval_eval


def test_offline_metric_is_labelled_bm25_not_hybrid(db, golden_novel):
    """The offline number must not present itself as hybrid retrieval quality.

    Both sides are hash vectors in this mode, so no embedding model is
    involved and the dense channel is active noise fused at equal weight.
    Reporting it as hybrid recall overstates what was measured; the label is
    the guard against that number being quoted.
    """
    report = run_retrieval_eval(db, golden_novel, k=8)
    assert report["metric"] == "bm25_recall_at_k"
    assert "bm25_recall_at_k" in report
    assert "hybrid_recall_at_k" not in report
    assert report["dense_channel"] == "hash (noise)"


@pytest.mark.skipif(
    os.getenv("RUN_LLM_EVALS") != "1",
    reason="real-embedding retrieval: set RUN_LLM_EVALS=1 (spends API credits)",
)
def test_hybrid_recall_with_real_embeddings(db):
    """The only mode whose number describes the system users actually run."""
    novel_id = ingest_fixture(db, use_mock_llm=False)
    try:
        report = run_retrieval_eval(db, novel_id, k=8, use_real_embeddings=True)
        print(f"\nhybrid recall@{report['k']}: {report['hybrid_recall_at_k']:.2f}")
        for row in report["per_query"]:
            print(f"  {row['recall_at_k']:.1f}  {row['query']!r} -> {row['got']}")
        assert report["metric"] == "hybrid_recall_at_k"
        assert report["dense_channel"] == "real"
        assert report["hybrid_recall_at_k"] >= 0.9, report["per_query"]
    finally:
        db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


def test_retrieval_recall_at_k(db, golden_novel):
    report = run_retrieval_eval(db, golden_novel, k=8)
    print(f"\nretrieval mean {report['metric']}@{report['k']}: {report['mean_recall_at_k']:.2f}")
    for row in report["per_query"]:
        print(f"  {row['recall_at_k']:.1f}  {row['query']!r} -> {row['got']}")
    # Mock-mode floor: dense vectors are hash noise, so this measures the
    # BM25/keyword half. The golden queries carry distinctive proper nouns;
    # this failing means retrieval (not the harness) regressed.
    #
    # Raised 0.5 -> 0.9 on 2026-08-14. The old floor was set while BM25 used
    # AND semantics and MMR reranked on query-embedding cosine, which scored
    # exactly 0.5; leaving it there would let that regression back in silently.
    # Full-recall queries score 1.0, so 0.9 tolerates one query slipping.
    assert report["mean_recall_at_k"] >= 0.9, report["per_query"]


def test_retrieval_report_shape(db, golden_novel):
    report = run_retrieval_eval(db, golden_novel, k=8)
    assert len(report["per_query"]) == 10
    assert 0.0 <= report["mean_recall_at_k"] <= 1.0
