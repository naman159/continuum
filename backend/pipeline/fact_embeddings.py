"""Backfill derived fact vectors, including historical canon payloads.

No extraction is rerun and chapter anchors are preserved. A novel lock keeps
backfill consistent with writers; failures roll back that novel's vector writes.
"""

import json
from typing import Any

from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService, canon_embedding_text, vector_literal


def backfill_fact_embeddings(db: DBClient, novel_id: str, service: EmbeddingService) -> dict[str, int]:
    counts = {"knowledge": 0, "canon_fact": 0, "commitment": 0, "canon_versions": 0}
    cache: dict[str, str] = {}

    def vector(text: str) -> str:
        if text not in cache:
            cache[text] = vector_literal(service.embed_text(text))
        return cache[text]

    with db.novel_lock(novel_id), db.session() as session:
        sources = {
            "knowledge": ("knows_edges", "SELECT k.id, k.fact_description AS text FROM knows_edges k JOIN characters c ON c.id = k.character_id WHERE c.novel_id = %s AND (k.embedding IS NULL OR k.embedding_model IS DISTINCT FROM %s)"),
            "canon_fact": ("canon_facts", "SELECT id, predicate || ': ' || value AS text FROM canon_facts WHERE novel_id = %s AND (embedding IS NULL OR embedding_model IS DISTINCT FROM %s)"),
            "commitment": ("commitments", "SELECT id, foreshadow_text AS text FROM commitments WHERE novel_id = %s AND (embedding IS NULL OR embedding_model IS DISTINCT FROM %s)"),
        }
        for kind, (table, sql) in sources.items():
            rows = session.fetchall(sql, (novel_id, service.model_name), dict_rows=True)
            for row in rows:
                session.execute(f"UPDATE {table} SET embedding = %s::vector, embedding_model = %s WHERE id = %s",
                                (vector(row["text"]), service.model_name, row["id"]))
                counts[kind] += 1
        versions = session.fetchall(
            """SELECT row_id, chapter_number, payload FROM story_record_versions
               WHERE novel_id = %s AND table_name = 'canon_facts' AND payload IS NOT NULL
                 AND (payload->>'embedding' IS NULL OR payload->>'embedding_model' IS DISTINCT FROM %s)""",
            (novel_id, service.model_name), dict_rows=True,
        )
        for version in versions:
            payload: dict[str, Any] = version["payload"]
            derived = {"embedding": vector(canon_embedding_text(payload["predicate"], payload["value"])),
                       "embedding_model": service.model_name}
            session.execute(
                """UPDATE story_record_versions SET payload = payload || %s::jsonb
                   WHERE novel_id = %s AND table_name = 'canon_facts' AND row_id = %s AND chapter_number = %s""",
                (json.dumps(derived), novel_id, version["row_id"], version["chapter_number"]),
            )
            counts["canon_versions"] += 1
    return counts
