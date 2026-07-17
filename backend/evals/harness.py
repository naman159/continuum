"""DB-facing half of the eval harness: ingest the golden fixture through the
real write spine and read back the projections the answer key grades.

Mock-LLM ingestion is deterministic and offline (hash embeddings, real
chunking/BM25) — it exercises the plumbing. Fidelity *thresholds* only mean
anything on a real-LLM run (see test_extraction_fidelity.py's env gate).
"""

from __future__ import annotations

from typing import Any

from evals.loader import load_answer_key, load_chapters
from evals.scoring import recall_at_k
from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService
from pipeline.pipeline import analyze_chapter
from pipeline.retrieval.hybrid import HybridRetriever
from reads.search import search


def ingest_fixture(db: DBClient, *, use_mock_llm: bool = True) -> str:
    """Create the golden novel and run every chapter through analyze_chapter."""
    key = load_answer_key()
    # commit=True is load-bearing: DBClient.fetchval defaults to ROLLBACK,
    # which would silently discard the INSERT.
    novel_id = str(db.fetchval(
        "INSERT INTO novels (title) VALUES (%s) RETURNING id",
        (key["novel"]["title"],),
        commit=True,
    ))
    # Each chapter commits its own transaction, so a mid-run failure would
    # otherwise strand a partially-ingested novel (callers never see the
    # novel_id to clean it up) — delete it before re-raising.
    try:
        for number, text in load_chapters():
            analyze_chapter(
                novel_id=novel_id,
                chapter_number=number,
                raw_text=text,
                chapter_title=None,
                use_mock_llm=use_mock_llm,
                # Pinned (not settings.chunk_size/overlap) so a developer's
                # CHUNK_SIZE env can't perturb chunking under the eval's
                # zero-margin recall floor.
                chunk_size=2000,
                chunk_overlap=200,
                db=db,
                replace=False,
                source="human",
            )
    except BaseException:
        db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))
        raise
    return novel_id


def read_projections(db: DBClient, novel_id: str) -> dict[str, Any]:
    """Read back the materialized projections the answer key grades."""
    def names(table: str) -> list[str]:
        return [
            r[0] for r in db.fetchall(
                f"SELECT name FROM {table} WHERE novel_id = %s ORDER BY name",
                (novel_id,),
            )
        ]

    possessions = [
        {"object": r["object"], "holder": r["holder"],
         "since": r["since_chapter"], "until": r["until_chapter"]}
        for r in db.fetchall(
            """
            SELECT o.name AS object, c.name AS holder,
                   p.since_chapter, p.until_chapter
              FROM possesses_edges p
              JOIN characters c ON c.id = p.character_id
              JOIN objects o ON o.id = p.object_id
             WHERE c.novel_id = %s
             ORDER BY p.since_chapter, o.name
            """,
            (novel_id,),
            dict_rows=True,
        )
    ]
    knowledge = [
        {"character": r["character"], "fact": r["fact_description"],
         "since": r["learned_chapter"]}
        for r in db.fetchall(
            """
            SELECT c.name AS character, k.fact_description, k.learned_chapter
              FROM knows_edges k
              JOIN characters c ON c.id = k.character_id
             WHERE c.novel_id = %s AND k.superseded_by_id IS NULL
             ORDER BY k.learned_chapter
            """,
            (novel_id,),
            dict_rows=True,
        )
    ]
    return {
        "entities": {
            "characters": names("characters"),
            "locations": names("locations"),
            "objects": names("objects"),
            "factions": names("factions"),
        },
        "possessions": possessions,
        "knowledge": knowledge,
    }


def run_retrieval_eval(db: DBClient, novel_id: str, k: int = 8) -> dict[str, Any]:
    """recall@k over the golden queries, via the production reads.search path.

    The retriever is injected with mock (hash) embeddings so the eval is
    offline and deterministic regardless of the environment's USE_MOCK_LLM:
    the fixture was ingested with hash embeddings, so real query embeddings
    would mismatch anyway. This grades the BM25/keyword half plus the full
    RRF/MMR plumbing.
    """
    key = load_answer_key()
    retriever = HybridRetriever(db, EmbeddingService(use_mock=True))
    per_query: list[dict[str, Any]] = []
    for q in key["queries"]:
        got = search(db, novel_id, q["text"], up_to_chapter=None, k=k, retriever=retriever)
        result_chapters = [
            r["chapter_number"] for r in got["results"]
            if r["chapter_number"] is not None
        ]
        score = recall_at_k(q["expect_chapters"], result_chapters, k)
        per_query.append({
            "query": q["text"],
            "expected": q["expect_chapters"],
            "got": result_chapters[:k],
            "recall_at_k": score,
        })
    mean = sum(p["recall_at_k"] for p in per_query) / len(per_query)
    return {"mean_recall_at_k": mean, "k": k, "per_query": per_query}
