"""Structured search candidates, reconstructed at the query's chapter cutoff.

Both retrieval channels use these same rows. In particular, neither ranking nor
returned records may see a later canon value, knowledge replacement, or payoff.
"""

from typing import Any

from pipeline.db.story_records import story_record_table
from pipeline.retrieval.types import RetrievalQuery

FACT_KINDS = ("knowledge", "canon_fact", "commitment")


def fact_source(query: RetrievalQuery, kind: str) -> str:
    # A None cutoff means the latest novel state, including manual metadata.
    # story_records_at accepts an integer; the largest chapter number gives that view.
    cap = query.max_chapter if query.max_chapter is not None else 2147483647
    chars = story_record_table("characters", query.novel_id, cap)
    if kind == "knowledge":
        return f"""
            SELECT k.id::text AS item_id, NULL::text AS chapter_id,
                   k.learned_chapter AS chapter_number,
                   c.name || ' knows: ' || k.fact_description AS snippet,
                   k.embedding, k.embedding_model,
                   jsonb_build_object('character_id', k.character_id,
                       'character_name', c.name, 'fact_description', k.fact_description,
                       'learned_chapter', k.learned_chapter, 'source_type', k.source_type,
                       'source_event_id', k.source_event_id, 'certainty', k.certainty) AS record
              FROM knows_edges k JOIN {chars} c ON c.id = k.character_id
             WHERE c.novel_id = %(novel_id)s
               AND (%(max_chapter)s::int IS NULL OR k.learned_chapter <= %(max_chapter)s)
               AND NOT EXISTS (SELECT 1 FROM knows_edges newer
                    WHERE newer.id = k.superseded_by_id
                      AND (%(max_chapter)s::int IS NULL OR newer.learned_chapter <= %(max_chapter)s))
        """
    if kind == "canon_fact":
        facts = story_record_table("canon_facts", query.novel_id, cap)
        entities = story_record_table("entities", query.novel_id, cap)
        return f"""
            SELECT cf.id::text AS item_id, NULL::text AS chapter_id,
                   cf.source_chapter AS chapter_number,
                   concat_ws(' ', e.name, cf.predicate || ': ' || cf.value) AS snippet,
                   cf.embedding, cf.embedding_model,
                   jsonb_build_object('subject_entity_id', cf.subject_entity_id,
                       'subject_name', e.name, 'kind', cf.kind, 'predicate', cf.predicate,
                       'value', cf.value, 'source_chapter', cf.source_chapter,
                       'confidence', cf.confidence, 'locked', cf.locked) AS record
              FROM {facts} cf LEFT JOIN {entities} e ON e.id = cf.subject_entity_id
             WHERE cf.novel_id = %(novel_id)s
               AND (%(max_chapter)s::int IS NULL OR cf.source_chapter IS NULL
                    OR cf.source_chapter <= %(max_chapter)s)
        """
    if kind == "commitment":
        return """
            SELECT c.id::text AS item_id, NULL::text AS chapter_id,
                   c.foreshadow_chapter AS chapter_number,
                   c.foreshadow_text AS snippet, c.embedding, c.embedding_model,
                   jsonb_build_object('foreshadow_text', c.foreshadow_text,
                       'foreshadow_chapter', c.foreshadow_chapter,
                       'foreshadow_event_id', c.foreshadow_event_id,
                       'status', CASE
                           WHEN c.status IN ('broken', 'abandoned') AND
                               (%(max_chapter)s::int IS NULL OR %(max_chapter)s >=
                                   (SELECT COALESCE(MAX(number), 0) FROM chapters WHERE novel_id = %(novel_id)s))
                               THEN c.status
                           WHEN c.payoff_chapter <= %(max_chapter)s OR
                               (%(max_chapter)s::int IS NULL AND c.payoff_chapter IS NOT NULL)
                               THEN 'satisfied' ELSE 'pending' END,
                       'payoff_text', CASE WHEN c.payoff_chapter <= %(max_chapter)s OR
                           (%(max_chapter)s::int IS NULL AND c.payoff_chapter IS NOT NULL)
                           THEN c.payoff_text END,
                       'payoff_chapter', CASE WHEN c.payoff_chapter <= %(max_chapter)s OR
                           (%(max_chapter)s::int IS NULL AND c.payoff_chapter IS NOT NULL)
                           THEN c.payoff_chapter END,
                       'weight', c.weight, 'related_entity_ids', c.related_entity_ids) AS record
              FROM commitments c WHERE c.novel_id = %(novel_id)s
               AND (%(max_chapter)s::int IS NULL OR c.foreshadow_chapter <= %(max_chapter)s)
        """
    raise ValueError(f"Unsupported structured retrieval kind: {kind}")


def fact_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {"record": row["record"], "_embedding": row.get("embedding"),
            "_embedding_model": row.get("embedding_model")}
