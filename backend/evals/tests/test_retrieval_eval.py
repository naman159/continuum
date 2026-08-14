from __future__ import annotations

from evals.harness import run_retrieval_eval


def test_retrieval_recall_at_k(db, golden_novel):
    report = run_retrieval_eval(db, golden_novel, k=8)
    print(f"\nretrieval mean recall@{report['k']}: {report['mean_recall_at_k']:.2f}")
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
