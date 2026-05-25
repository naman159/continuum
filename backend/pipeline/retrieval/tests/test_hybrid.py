from __future__ import annotations

import os
import uuid

import pytest

from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, vector_literal
from pipeline.retrieval import HybridRetriever, RetrievalQuery
from pipeline.retrieval.bm25 import BM25Search
from pipeline.retrieval.dense import DenseSearch
from pipeline.retrieval.fusion import reciprocal_rank_fusion
from pipeline.retrieval.mmr import mmr
from pipeline.retrieval.rerank import LLMReranker
from pipeline.retrieval.types import RetrievalResult


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


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
            INSERT INTO chapters (id, novel_id, number, title, raw_text, summary, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
            """,
            (chapter_id, novel_id, number, title, raw_text, summary, emb),
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
            INSERT INTO scenes (id, chapter_id, scene_index, summary, embedding)
            VALUES (%s, %s, %s, %s, %s::vector)
            """,
            (sid, chapter_id, scene_index, summary, emb),
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
            INSERT INTO events (id, chapter_id, description, embedding)
            VALUES (%s, %s, %s, %s::vector)
            """,
            (eid, chapter_id, description, emb),
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


def test_dense_event_with_entity_filter_empty_safe(db, embedder, seeded):
    dense = DenseSearch(db, embedder)
    query = RetrievalQuery(
        text="dragon raid",
        novel_id=seeded["novel_id"],
        entity_ids=[str(uuid.uuid4())],  # no overlap with anything
    )
    results = dense.search(query, kind="event", limit=10)
    # Entity filter excludes everything; should return empty cleanly.
    assert results == []


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
    q = [1.0, 0.0]
    e1 = [1.0, 0.0]
    e2 = [0.99, 0.01]  # near-duplicate of e1
    e3 = [0.0, 1.0]    # different
    cands = [_mk("a", score=0.9), _mk("b", score=0.89), _mk("c", score=0.5)]
    embeddings = {"a": e1, "b": e2, "c": e3}
    # lambda=0.3 puts more weight on diversity. With e2≈e1, "b" is a near-duplicate
    # of "a", so MMR should prefer the orthogonal "c" even though "c" is less
    # relevant to q. At lambda=0.5 the math ties (both score 0), so this test
    # uses 0.3 to verify diversity actually dominates when configured to.
    out = mmr(q, cands, embeddings, lambda_=0.3, top_k=2)
    ids = [r.item_id for r in out]
    assert ids[0] == "a"
    assert ids[1] == "c"


# ---- Hybrid orchestrator ----


def test_hybrid_retrieve_returns_relevant_items(db, embedder, seeded):
    retriever = HybridRetriever(db, embedder, reranker=None)
    query = RetrievalQuery(
        text="dragon raids Pellis harbor",
        novel_id=seeded["novel_id"],
        k=5,
    )
    bundle = retriever.retrieve(query, kinds=["chapter", "scene", "event"])
    assert bundle.results
    assert "timings_ms" in bundle.debug
    assert "stages" in bundle.debug
    # The dragon-chapter / dragon-event should be in the top results.
    dragon_chapter = seeded["chapter_ids"][2]
    dragon_event = seeded["event_ids"][2]
    ids = {r.item_id for r in bundle.results}
    assert (dragon_chapter in ids) or (dragon_event in ids)


def test_hybrid_respects_max_chapter(db, embedder, seeded):
    retriever = HybridRetriever(db, embedder, reranker=None)
    query = RetrievalQuery(
        text="dragon Pellis",
        novel_id=seeded["novel_id"],
        max_chapter=1,
        k=5,
    )
    bundle = retriever.retrieve(query, kinds=["chapter", "event"])
    for r in bundle.results:
        if r.chapter_number is not None:
            assert r.chapter_number <= 1


# ---- Reranker (requires API credentials) ----


@pytest.mark.skipif(not GEMINI_API_KEY, reason="No Gemini API key available")
def test_reranker_orders_candidates(db, embedder, seeded):
    retriever = HybridRetriever(db, embedder, reranker=LLMReranker())
    query = RetrievalQuery(
        text="Who is the dragon attacking the harbor?",
        novel_id=seeded["novel_id"],
        k=3,
    )
    bundle = retriever.retrieve(query, kinds=["chapter", "event"])
    assert bundle.results
    assert "rerank" in bundle.debug.get("stages", {})
