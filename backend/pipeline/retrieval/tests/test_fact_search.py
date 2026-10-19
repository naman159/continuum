"""Structured retrieval against PostgreSQL; no paid embedding/model calls."""
from uuid import uuid4

import pytest

from pipeline.db.story_records import capture_story_records
from pipeline.embeddings import EmbeddingService, vector_literal
from pipeline.fact_embeddings import backfill_fact_embeddings
from pipeline.retrieval.bm25 import BM25Search
from pipeline.retrieval.dense import DenseSearch
from pipeline.retrieval.hybrid import HybridRetriever
from pipeline.retrieval.types import RetrievalQuery
from reads.search import search


@pytest.fixture
def story(db, seed_novel):
    seeded = seed_novel(db)
    seeded["knowledge_id"] = str(db.fetchval(
        """INSERT INTO knows_edges(character_id, fact_description, learned_chapter, source_type)
           VALUES (%s, 'Jon passed secrets to the enemy', 1, 'witnessed') RETURNING id""",
        (seeded["char_a_id"],), commit=True))
    seeded["embedder"] = EmbeddingService(use_mock=True)
    return seeded


def test_fact_vectors_backfill_existing_and_historical_records(db, story):
    capture_story_records(db, story["novel_id"], 2)
    counts = backfill_fact_embeddings(db, story["novel_id"], story["embedder"])
    assert counts == {"knowledge": 1, "canon_fact": 1, "commitment": 1, "canon_versions": 1}
    assert backfill_fact_embeddings(db, story["novel_id"], story["embedder"]) == dict.fromkeys(counts, 0)


@pytest.mark.parametrize("kind,text", [
    ("knowledge", "secrets enemy"), ("canon_fact", "green"), ("commitment", "compass"),
])
def test_keyword_fact_candidates_have_records_without_embeddings(db, story, kind, text):
    # Fact vectors need not exist for keyword retrieval to work.
    if kind == "canon_fact":
        text = db.fetchval("SELECT value FROM canon_facts WHERE id = %s", (story["canon_fact_id"],))
    if kind == "commitment":
        text = db.fetchval("SELECT foreshadow_text FROM commitments WHERE id = %s", (story["commitment_id"],))
    rows = BM25Search(db).search(RetrievalQuery(text=text, novel_id=story["novel_id"], max_chapter=2), kind)
    assert rows
    assert rows[0].metadata["record"]
    assert rows[0].item_id


def test_dense_discovery_uses_fact_vectors_without_keyword_overlap(db, story):
    # Controlled vectors test channel wiring, not the quality of a real model.
    # "betrayal" occurs nowhere in the stored knowledge text.
    query_vec = [1.0] + [0.0] * (len(story["embedder"].embed_text("x")) - 1)
    db.execute("UPDATE knows_edges SET embedding = %s::vector, embedding_model = %s WHERE id = %s",
               (vector_literal(query_vec), story["embedder"].model_name, story["knowledge_id"]))
    query = RetrievalQuery(text="betrayal", novel_id=story["novel_id"], max_chapter=2)
    assert BM25Search(db).search(query, "knowledge") == []
    rows = DenseSearch(db, story["embedder"]).search(query, "knowledge", query_embedding=query_vec)
    assert rows[0].item_id == story["knowledge_id"]
    db.execute("UPDATE knows_edges SET embedding_model = 'different-model' WHERE id = %s", (story["knowledge_id"],))
    assert DenseSearch(db, story["embedder"]).search(query, "knowledge", query_embedding=query_vec) == []


@pytest.mark.parametrize("channel", ["keyword", "dense"])
def test_historical_canon_value_and_vector_are_retrieved_together(db, story, channel):
    capture_story_records(db, story["novel_id"], 2)
    db.execute("UPDATE canon_facts SET value = 'future-only-secret', source_chapter = 3 WHERE id = %s",
               (story["canon_fact_id"],))
    capture_story_records(db, story["novel_id"], 3)
    backfill_fact_embeddings(db, story["novel_id"], story["embedder"])
    retriever = BM25Search(db) if channel == "keyword" else DenseSearch(db, story["embedder"])
    before = retriever.search(RetrievalQuery(text="future-only-secret", novel_id=story["novel_id"], max_chapter=2), "canon_fact")
    if channel == "keyword":
        assert before == []
        predicate = db.fetchval("SELECT predicate FROM canon_facts WHERE id = %s", (story["canon_fact_id"],))
        before = retriever.search(RetrievalQuery(text=predicate, novel_id=story["novel_id"], max_chapter=2), "canon_fact")
    assert before
    assert before[0].metadata["record"]["value"] != "future-only-secret"
    assert before[0].chapter_number == 2
    after = retriever.search(RetrievalQuery(text="future-only-secret", novel_id=story["novel_id"], max_chapter=3), "canon_fact")
    assert after[0].metadata["record"]["value"] == "future-only-secret"
    assert before[0].metadata["_embedding"] != after[0].metadata["_embedding"]
    # Diversification must use the historical vector returned by the candidate.
    vectors = HybridRetriever(db, story["embedder"])._fetch_item_embeddings(before)
    expected = [float(x) for x in before[0].metadata["_embedding"].strip("[]").split(",")]
    assert vectors[before[0].item_id] == pytest.approx(expected)


@pytest.mark.parametrize("channel", ["keyword", "dense"])
def test_knowledge_cutoff_replacement_and_novel_isolation(db, story, channel):
    replacement = db.fetchval(
        """INSERT INTO knows_edges(character_id, fact_description, learned_chapter)
           VALUES (%s, 'Jon never shared secrets', 3) RETURNING id""",
        (story["char_a_id"],), commit=True)
    db.execute("UPDATE knows_edges SET superseded_by_id = %s WHERE id = %s", (replacement, story["knowledge_id"]))
    backfill_fact_embeddings(db, story["novel_id"], story["embedder"])
    retriever = BM25Search(db) if channel == "keyword" else DenseSearch(db, story["embedder"])
    query = RetrievalQuery(text="secrets", novel_id=story["novel_id"], max_chapter=2)
    assert [r.item_id for r in retriever.search(query, "knowledge")] == [story["knowledge_id"]]
    query.max_chapter = 3
    assert [r.item_id for r in retriever.search(query, "knowledge")] == [str(replacement)]
    query.novel_id = str(uuid4())
    assert retriever.search(query, "knowledge") == []


@pytest.mark.parametrize("channel", ["keyword", "dense"])
def test_commitment_payoff_is_neither_ranked_nor_returned_before_cutoff(db, story, channel):
    db.execute("UPDATE commitments SET payoff_text = 'future-only-secret' WHERE id = %s", (story["commitment_id"],))
    backfill_fact_embeddings(db, story["novel_id"], story["embedder"])
    retriever = BM25Search(db) if channel == "keyword" else DenseSearch(db, story["embedder"])
    text = db.fetchval("SELECT foreshadow_text FROM commitments WHERE id = %s", (story["commitment_id"],))
    query = RetrievalQuery(text=text, novel_id=story["novel_id"], max_chapter=2)
    record = retriever.search(query, "commitment")[0].metadata["record"]
    assert record["status"] == "pending"
    assert record["payoff_text"] is None and record["payoff_chapter"] is None
    query.max_chapter = 3
    assert retriever.search(query, "commitment")[0].metadata["record"]["payoff_text"] == "future-only-secret"
    if channel == "keyword":
        query.text = "future-only-secret"
        query.max_chapter = 2
        assert retriever.search(query, "commitment") == []


def test_search_returns_structured_records_and_no_internal_vectors(db, story):
    backfill_fact_embeddings(db, story["novel_id"], story["embedder"])
    result = search(db, story["novel_id"], "Jon passed secrets to the enemy", 2,
                    retriever=HybridRetriever(db, story["embedder"]), k=20)
    hit = next(r for r in result["results"] if r["item_id"] == story["knowledge_id"])
    assert hit["record"]["character_name"] == story["char_a_name"]
    assert hit["record"]["source_type"] == "witnessed"
    assert "embedding" not in str(result)


def test_text_edit_invalidates_stale_fact_vectors(db, story):
    backfill_fact_embeddings(db, story["novel_id"], story["embedder"])
    db.execute("UPDATE knows_edges SET fact_description = 'new fact' WHERE id = %s", (story["knowledge_id"],))
    db.execute("UPDATE canon_facts SET value = 'new value' WHERE id = %s", (story["canon_fact_id"],))
    assert db.fetchval("SELECT embedding FROM knows_edges WHERE id = %s", (story["knowledge_id"],)) is None
    assert db.fetchval("SELECT embedding FROM canon_facts WHERE id = %s", (story["canon_fact_id"],)) is None


def test_extracted_fact_writers_generate_vectors(db, story):
    from pipeline.extraction.persist_canon import persist_canon_facts
    from pipeline.extraction.persist_extras import persist_knows_edges
    from pipeline.extraction.resolver import EntityResolver

    resolver = EntityResolver(db, story["novel_id"], 2)
    knowledge_ids = persist_knows_edges(
        db, chapter_number=2, resolver=resolver, embedder=story["embedder"],
        learnings=[{"character_name": story["char_a_name"], "fact_description": "The gate is guarded"}],
    )
    assert db.fetchval("SELECT embedding_model FROM knows_edges WHERE id = %s", (knowledge_ids[0],)) == "mock:hash"
    persist_canon_facts(
        db, novel_id=story["novel_id"], chapter_id=story["chapter_ids"][1], chapter_number=2,
        resolver=resolver, embedder=story["embedder"],
        facts=[{"subject_name": story["char_a_name"], "subject_type": "character", "predicate": "home", "value": "Moonspire"}],
    )
    old = db.fetchval("SELECT embedding FROM canon_facts WHERE novel_id = %s AND predicate = 'home'", (story["novel_id"],))
    assert old is not None
    persist_canon_facts(
        db, novel_id=story["novel_id"], chapter_id=story["chapter_ids"][2], chapter_number=3,
        resolver=resolver, embedder=story["embedder"],
        facts=[{"subject_name": story["char_a_name"], "subject_type": "character", "predicate": "home", "value": "Sunspire"}],
    )
    new = db.fetchval("SELECT embedding FROM canon_facts WHERE novel_id = %s AND predicate = 'home'", (story["novel_id"],))
    assert new is not None and new != old
