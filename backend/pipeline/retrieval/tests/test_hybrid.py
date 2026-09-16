from __future__ import annotations

import uuid

import pytest

from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, vector_literal
from pipeline.retrieval import HybridRetriever, RetrievalQuery
from pipeline.retrieval.bm25 import BM25Search
from pipeline.retrieval.dense import DenseSearch
from pipeline.retrieval.fusion import reciprocal_rank_fusion
from pipeline.retrieval.mmr import mmr
from pipeline.retrieval.types import RetrievalResult


# ---- Fixtures: seed a small synthetic novel into the branch DB ----


@pytest.fixture(scope="module")
def db() -> DBClient:
    client = DBClient()
    yield client
    client.close()


@pytest.fixture(scope="module")
def embedder() -> EmbeddingService:
    # Force the deterministic hash embedder so dense search is reproducible
    # without network calls. The HybridRetriever uses cosine distance, which
    # works on any vector space; hash embeddings cluster similar text well
    # enough for sanity asserts.
    return EmbeddingService(use_mock=True)


@pytest.fixture(scope="module")
def seeded(db: DBClient, embedder: EmbeddingService):
    novel_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO novels (id, title, author) VALUES (%s, %s, %s)",
        (novel_id, f"Retrieval Test Novel {novel_id[:8]}", "tester"),
    )

    chapters = [
        (
            1,
            "Chapter One",
            "Aldric the silver-haired warden patrols the frostbitten ramparts of "
            "Cair Eldoth, scanning the snow for direwolves.",
            "Aldric patrols the ramparts of Cair Eldoth at night and spots wolf tracks.",
        ),
        (
            2,
            "Chapter Two",
            "Lady Sereth brews a tincture of moonbell in the apothecary tower, "
            "whispering about a poisoned chalice meant for the high steward.",
            "Sereth prepares poison in the apothecary tower while plotting against the steward.",
        ),
        (
            3,
            "Chapter Three",
            "A dragon named Vextheryon descends upon the harbor town of Pellis, "
            "burning fishing boats and stealing gilded crowns from the customs house.",
            "Vextheryon the dragon raids Pellis harbor and burns ships.",
        ),
    ]

    chapter_ids: list[str] = []
    for number, title, raw_text, summary in chapters:
        chapter_id = str(uuid.uuid4())
        chapter_ids.append(chapter_id)
        emb = vector_literal(embedder.embed_text(summary))
        db.execute(
            """
            INSERT INTO chapters (id, novel_id, number, title, raw_text, summary,
                                  embedding, embedding_model)
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s)
            """,
            (chapter_id, novel_id, number, title, raw_text, summary, emb,
             embedder.model_name),
        )

    # Scenes (one per chapter for simplicity)
    scenes_data = [
        (chapter_ids[0], 0, "Aldric walks the icy ramparts and sees direwolf paw prints in fresh snow."),
        (chapter_ids[1], 0, "Sereth grinds moonbell petals into a chalice intended for the high steward."),
        (chapter_ids[2], 0, "Vextheryon the dragon breathes fire on Pellis harbor and seizes a crown."),
    ]
    scene_ids: list[str] = []
    for chapter_id, scene_index, summary in scenes_data:
        sid = str(uuid.uuid4())
        scene_ids.append(sid)
        emb = vector_literal(embedder.embed_text(summary))
        db.execute(
            """
            INSERT INTO scenes (id, chapter_id, scene_index, summary,
                                embedding, embedding_model)
            VALUES (%s, %s, %s, %s, %s::vector, %s)
            """,
            (sid, chapter_id, scene_index, summary, emb, embedder.model_name),
        )

    # Events
    events_data = [
        (chapter_ids[0], "Aldric the warden spots direwolf tracks in the snow outside Cair Eldoth."),
        (chapter_ids[1], "Lady Sereth prepares a moonbell poison for the high steward."),
        (chapter_ids[2], "Vextheryon the dragon raids the harbor at Pellis and burns boats."),
    ]
    event_ids: list[str] = []
    for chapter_id, description in events_data:
        eid = str(uuid.uuid4())
        event_ids.append(eid)
        emb = vector_literal(embedder.embed_text(description))
        db.execute(
            """
            INSERT INTO events (id, chapter_id, description,
                                embedding, embedding_model)
            VALUES (%s, %s, %s, %s::vector, %s)
            """,
            (eid, chapter_id, description, emb, embedder.model_name),
        )

    yield {
        "novel_id": novel_id,
        "chapter_ids": chapter_ids,
        "scene_ids": scene_ids,
        "event_ids": event_ids,
    }

    # Teardown: cascading deletes via novels.
    db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))


# ---- BM25 ----


def test_bm25_chapter_literal_token(db, seeded):
    bm25 = BM25Search(db)
    query = RetrievalQuery(text="Vextheryon dragon Pellis", novel_id=seeded["novel_id"])
    results = bm25.search(query, kind="chapter", limit=10)
    assert results, "BM25 should return at least one chapter for a literal token query"
    assert results[0].chapter_id == seeded["chapter_ids"][2]
    assert 0.0 <= results[0].score <= 1.0


def test_bm25_event_filter_by_max_chapter(db, seeded):
    bm25 = BM25Search(db)
    query = RetrievalQuery(
        text="moonbell poison steward",
        novel_id=seeded["novel_id"],
        max_chapter=1,
    )
    results = bm25.search(query, kind="event", limit=10)
    # Chapter 2 contains the match; max_chapter=1 should exclude it.
    assert all((r.chapter_number or 0) <= 1 for r in results)


def test_bm25_matches_when_only_some_terms_present(db, seeded):
    # Natural-language queries rarely have every term in the target document.
    # AND semantics (plainto_tsquery) drop these to zero hits; the ranker is
    # responsible for pushing partial matches down, not the matcher.
    bm25 = BM25Search(db)
    query = RetrievalQuery(
        text="Vextheryon bicycle telephone", novel_id=seeded["novel_id"]
    )
    results = bm25.search(query, kind="chapter", limit=10)
    assert results, "BM25 should match on the terms that are present"
    assert results[0].chapter_id == seeded["chapter_ids"][2]


def test_bm25_ranks_full_matches_above_partial_matches(db, seeded):
    bm25 = BM25Search(db)
    query = RetrievalQuery(
        text="Vextheryon dragon harbor moonbell", novel_id=seeded["novel_id"]
    )
    results = bm25.search(query, kind="chapter", limit=10)
    ids = [r.chapter_id for r in results]
    # Chapter 3 matches three terms, chapter 2 only "moonbell".
    assert ids.index(seeded["chapter_ids"][2]) < ids.index(seeded["chapter_ids"][1])


def test_bm25_tolerates_tsquery_punctuation(db, seeded):
    # Raw user input reaches the matcher; operator characters must not
    # produce a syntax error.
    bm25 = BM25Search(db)
    for text in ["what happened to Jake's bow?", "dragon & (harbor | :*", "!!!", "a <-> b"]:
        query = RetrievalQuery(text=text, novel_id=seeded["novel_id"])
        bm25.search(query, kind="chapter", limit=10)


def test_bm25_scene_hits_target_scene(db, seeded):
    bm25 = BM25Search(db)
    query = RetrievalQuery(text="moonbell chalice", novel_id=seeded["novel_id"])
    results = bm25.search(query, kind="scene", limit=10)
    assert results
    assert results[0].item_id == seeded["scene_ids"][1]


# ---- Dense ----


def test_dense_chapter_returns_results(db, embedder, seeded):
    dense = DenseSearch(db, embedder)
    # Using the exact summary text guarantees the hash embedder produces an
    # identical vector, so cosine distance ~ 0 and similarity ~ 1.
    query = RetrievalQuery(
        text="Vextheryon the dragon raids Pellis harbor and burns ships.",
        novel_id=seeded["novel_id"],
    )
    results = dense.search(query, kind="chapter", limit=10)
    assert results
    assert results[0].chapter_id == seeded["chapter_ids"][2]
    assert results[0].score > 0.99  # near-identical vector


def test_dense_excludes_vectors_from_a_different_embedding_model(db, embedder, seeded):
    # A hash vector and a real vector are indistinguishable once written; the
    # provenance column is the only thing that tells them apart, so dense
    # search has to actually consult it.
    text = "Aldric patrols the ramparts of Cair Eldoth at night and spots wolf tracks."
    foreign_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO chapters (id, novel_id, number, title, raw_text, summary,
                              embedding, embedding_model)
        VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s)
        """,
        (foreign_id, seeded["novel_id"], 99, "Foreign", text, text,
         vector_literal(embedder.embed_text(text)), "some-other-model"),
    )
    try:
        dense = DenseSearch(db, embedder)
        query = RetrievalQuery(text=text, novel_id=seeded["novel_id"])
        found = [r.item_id for r in dense.search(query, kind="chapter", limit=50)]
        # Identical text, so it would otherwise rank at distance ~0.
        assert foreign_id not in found
        assert found, "matching-provenance chapters should still come back"
    finally:
        db.execute("DELETE FROM chapters WHERE id = %s", (foreign_id,))


class _TiedRowsDB:
    """Returns equal-distance rows in whatever order it is told to."""

    def __init__(self, order: list[str]) -> None:
        self.order = order

    def fetchall(self, sql, params=None, dict_rows=False):
        return [
            {
                "item_id": i,
                "chapter_id": None,
                "chapter_number": 1,
                "snippet": "",
                "distance": 0.25,
            }
            for i in self.order
        ]


def test_dense_breaks_distance_ties_deterministically(embedder):
    # RRF consumes rank, not score, so an arbitrary order among equal
    # distances would propagate into the fused ranking.
    forward = DenseSearch(_TiedRowsDB(["a", "b", "c"]), embedder)
    reverse = DenseSearch(_TiedRowsDB(["c", "b", "a"]), embedder)
    query = RetrievalQuery(text="anything", novel_id=str(uuid.uuid4()))
    ids = lambda d: [r.item_id for r in d.search(query, kind="chapter", limit=10)]
    assert ids(forward) == ids(reverse) == ["a", "b", "c"]


# ---- Fusion ----


def _mk(item_id: str, kind: str = "event", score: float = 1.0) -> RetrievalResult:
    return RetrievalResult(item_id=item_id, kind=kind, score=score, snippet="")


def test_rrf_combines_and_deduplicates():
    a = [_mk("x"), _mk("y"), _mk("z")]
    b = [_mk("y"), _mk("x"), _mk("w")]
    fused = reciprocal_rank_fusion(a, b, k=60, limit=10)
    ids = [r.item_id for r in fused]
    assert set(ids) == {"x", "y", "z", "w"}
    # Items in both lists should rank above items in only one.
    assert ids.index("x") < ids.index("z")
    assert ids.index("y") < ids.index("w")


def test_rrf_empty_inputs():
    assert reciprocal_rank_fusion([], [], k=60, limit=10) == []


# ---- MMR ----


def test_mmr_diversifies_results():
    e1 = [1.0, 0.0]
    e2 = [0.99, 0.01]  # near-duplicate of e1
    e3 = [0.0, 1.0]    # different
    cands = [_mk("a", score=0.9), _mk("b", score=0.89), _mk("c", score=0.5)]
    embeddings = {"a": e1, "b": e2, "c": e3}
    # lambda=0.3 puts more weight on diversity. With e2≈e1, "b" is a near-duplicate
    # of "a", so MMR should prefer the orthogonal "c" even though "c" is more
    # relevant. At lambda=0.5 the math ties (both score 0), so this test
    # uses 0.3 to verify diversity actually dominates when configured to.
    out = mmr(cands, embeddings, lambda_=0.3, top_k=2)
    ids = [r.item_id for r in out]
    assert ids[0] == "a"
    assert ids[1] == "c"


def test_mmr_relevance_comes_from_fused_score():
    # The fused score is the hybrid signal (BM25 + dense). MMR must rank on it
    # rather than recomputing similarity from the embedding, which would
    # discard the keyword half of the hybrid.
    cands = [_mk("keyword_winner", score=1.0), _mk("dense_only", score=0.2)]
    embeddings = {"keyword_winner": [0.0, 1.0], "dense_only": [1.0, 0.0]}
    out = mmr(cands, embeddings, lambda_=1.0, top_k=2)
    assert [r.item_id for r in out] == ["keyword_winner", "dense_only"]


def test_mmr_ranks_candidates_without_embeddings_on_the_same_scale():
    # A strong keyword hit whose embedding is missing must still outrank a
    # weak candidate that happens to have one.
    cands = [_mk("no_embedding", score=1.0), _mk("has_embedding", score=0.1)]
    out = mmr(cands, {"has_embedding": [1.0, 0.0]}, lambda_=0.7, top_k=2)
    assert [r.item_id for r in out] == ["no_embedding", "has_embedding"]


def test_mmr_reports_its_ranking_score():
    cands = [_mk("a", score=0.9), _mk("b", score=0.8), _mk("c", score=0.7)]
    embeddings = {"a": [1.0, 0.0], "b": [0.99, 0.01], "c": [0.0, 1.0]}
    out = mmr(cands, embeddings, lambda_=0.7, top_k=3)
    scores = [r.score for r in out]
    assert scores == sorted(scores, reverse=True), "score must match the emitted order"
    # The pre-diversification signal stays available for debugging.
    assert "rrf_score" in out[0].metadata or "mmr_relevance" in out[0].metadata


# ---- Hybrid orchestrator ----


def test_hybrid_retrieve_returns_relevant_items(db, embedder, seeded):
    retriever = HybridRetriever(db, embedder)
    query = RetrievalQuery(
        text="dragon raids Pellis harbor",
        novel_id=seeded["novel_id"],
        k=5,
    )
    results = retriever.retrieve(query)
    assert results
    # The dragon material must lead, not merely appear somewhere in the page.
    dragon_items = {
        seeded["chapter_ids"][2],
        seeded["scene_ids"][2],
        seeded["event_ids"][2],
    }
    assert results[0].item_id in dragon_items


def test_hybrid_scores_are_ordered(db, embedder, seeded):
    retriever = HybridRetriever(db, embedder)
    query = RetrievalQuery(
        text="dragon raids Pellis harbor", novel_id=seeded["novel_id"], k=5
    )
    results = retriever.retrieve(query)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_keeps_agreed_result_on_top(db, embedder, seeded):
    # An item both BM25 and dense rank highly wins RRF; the diversification
    # stage must not demote it below dense-only candidates.
    retriever = HybridRetriever(db, embedder)
    query = RetrievalQuery(
        text="Vextheryon dragon Pellis", novel_id=seeded["novel_id"], k=8
    )
    # Recompute the fused ranking from the same two sources the retriever
    # uses, so the assertion is about MMR not demoting the agreed winner
    # rather than about any particular item.
    lists = []
    for kind in ("chapter", "scene", "event"):
        lists.append(retriever.bm25.search(query, kind, 50))
        lists.append(retriever.dense.search(query, kind, 50))
    top_fused = reciprocal_rank_fusion(*lists, k=60, limit=50)[0].item_id

    results = retriever.retrieve(query)
    assert results[0].item_id == top_fused


def test_hybrid_respects_max_chapter(db, embedder, seeded):
    retriever = HybridRetriever(db, embedder)
    query = RetrievalQuery(
        text="dragon Pellis",
        novel_id=seeded["novel_id"],
        max_chapter=1,
        k=5,
    )
    results = retriever.retrieve(query)
    for r in results:
        if r.chapter_number is not None:
            assert r.chapter_number <= 1


def test_embedding_outage_preserves_keyword_results(db, embedder, seeded, monkeypatch):
    retriever = HybridRetriever(db, embedder)

    def unavailable(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    def unexpected_dense(*args, **kwargs):
        pytest.fail("dense search must not retry a failed query embedding")

    monkeypatch.setattr(embedder, "embed_text", unavailable)
    monkeypatch.setattr(retriever.dense, "search", unexpected_dense)
    results = retriever.retrieve(RetrievalQuery(
        text="Vextheryon dragon Pellis", novel_id=seeded["novel_id"], max_chapter=3,
    ))
    assert results
    assert results[0].chapter_number == 3


def test_all_stage_failures_are_not_reported_as_no_matches(embedder, monkeypatch):
    retriever = HybridRetriever(None, embedder)

    def unavailable(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(retriever.bm25, "search", unavailable)
    monkeypatch.setattr(retriever.dense, "search", unavailable)
    with pytest.raises(RuntimeError, match="All retrieval stages failed"):
        retriever.retrieve(RetrievalQuery(text="dragon", novel_id=str(uuid.uuid4())))


def test_diversification_outage_preserves_fused_results(db, embedder, seeded, monkeypatch):
    retriever = HybridRetriever(db, embedder)
    query = RetrievalQuery(text="dragon Pellis", novel_id=seeded["novel_id"], k=3)
    expected = retriever.retrieve(query, use_mmr=False)

    def unavailable(*args, **kwargs):
        raise RuntimeError("embedding lookup failed")

    monkeypatch.setattr(retriever, "_fetch_item_embeddings", unavailable)
    assert retriever.retrieve(query) == expected


def test_diversification_excludes_incompatible_vectors(db, embedder, seeded):
    chapter_id = seeded["chapter_ids"][0]
    db.execute("UPDATE chapters SET embedding_model = 'other-model' WHERE id = %s", (chapter_id,))
    try:
        retriever = HybridRetriever(db, embedder)
        assert retriever._fetch_item_embeddings([_mk(chapter_id, kind="chapter")]) == {}
    finally:
        db.execute(
            "UPDATE chapters SET embedding_model = %s WHERE id = %s",
            (embedder.model_name, chapter_id),
        )
