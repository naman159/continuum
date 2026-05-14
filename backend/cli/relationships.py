from __future__ import annotations

import argparse
import json
from typing import Any

from pipeline.db.client import DBClient


def build_relationship_graph(
    novel_id: str,
    *,
    up_to_chapter: int | None = None,
) -> dict[str, Any]:
    with DBClient() as db:
        params: list[Any] = [novel_id]
        chapter_filter = ""
        if up_to_chapter is not None:
            chapter_filter = "AND (r.from_chapter IS NULL OR r.from_chapter <= %s)"
            params.append(up_to_chapter)

        char_rows = db.fetchall(
            """
            SELECT c.id, e.name
            FROM entities e
            JOIN characters c ON c.entity_id = e.id
            WHERE e.novel_id = %s AND e.entity_type = 'character'
            ORDER BY e.name
            """,
            (novel_id,),
            dict_rows=True,
        )
        nodes = [{"id": str(row["id"]), "label": row["name"]} for row in char_rows]
        character_ids = {row["id"] for row in char_rows}

        edge_rows = db.fetchall(
            f"""
            SELECT r.id, ca.id AS char_a_id, cb.id AS char_b_id,
                   r.rel_type, r.from_chapter, r.notes
            FROM relationships r
            JOIN entities ea ON ea.id = r.entity_a_id AND ea.novel_id = %s AND ea.entity_type = 'character'
            JOIN entities eb ON eb.id = r.entity_b_id AND eb.entity_type = 'character'
            JOIN characters ca ON ca.entity_id = ea.id
            JOIN characters cb ON cb.entity_id = eb.id
            {chapter_filter}
            ORDER BY r.from_chapter NULLS LAST, r.created_at
            """,
            tuple(params),
            dict_rows=True,
        )

        return {
            "nodes": nodes,
            "edges": [
                {
                    "id": str(row["id"]),
                    "source": str(row["char_a_id"]),
                    "target": str(row["char_b_id"]),
                    "rel_type": row["rel_type"],
                    "chapter_number": row["from_chapter"],
                    "notes": row["notes"],
                }
                for row in edge_rows
                if row["char_a_id"] in character_ids and row["char_b_id"] in character_ids
            ],
            "up_to_chapter": up_to_chapter,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build relationship graph JSON")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--up-to-chapter", type=int)
    args = parser.parse_args()

    payload = build_relationship_graph(
        args.novel_id,
        up_to_chapter=args.up_to_chapter,
    )
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
