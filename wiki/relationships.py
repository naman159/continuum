from __future__ import annotations

import argparse
import json
from typing import Any

from db.client import DBClient


def build_relationship_graph(
    novel_id: str,
    *,
    up_to_chapter: int | None = None,
) -> dict[str, Any]:
    with DBClient() as db:
        nodes: list[dict[str, Any]] = []

        for entity_type, table in (
            ("character", "characters"),
            ("location", "locations"),
            ("faction", "factions"),
            ("object", "objects"),
        ):
            rows = db.fetchall(
                f"""
                SELECT id, name
                FROM {table}
                WHERE novel_id = %s
                ORDER BY name
                """,
                (novel_id,),
                dict_rows=True,
            )
            nodes.extend(
                {
                    "id": str(row["id"]),
                    "label": row["name"],
                    "type": entity_type,
                }
                for row in rows
            )

        params: list[Any] = [novel_id]
        chapter_filter = ""
        if up_to_chapter is not None:
            chapter_filter = " AND (ch.number <= %s OR ch.number IS NULL)"
            params.append(up_to_chapter)

        edges = db.fetchall(
            f"""
            SELECT r.id,
                   r.entity_a_id,
                   r.entity_a_type,
                   r.entity_b_id,
                   r.entity_b_type,
                   r.rel_type,
                   r.status,
                   r.notes,
                   ch.number AS chapter_number
            FROM relationships r
            LEFT JOIN chapters ch ON ch.id = r.chapter_id
            JOIN novels n ON n.id = %s
            WHERE (
                (r.entity_a_type = 'character' AND EXISTS (
                    SELECT 1 FROM characters c WHERE c.id = r.entity_a_id AND c.novel_id = n.id
                ))
                OR (r.entity_a_type = 'location' AND EXISTS (
                    SELECT 1 FROM locations l WHERE l.id = r.entity_a_id AND l.novel_id = n.id
                ))
                OR (r.entity_a_type = 'faction' AND EXISTS (
                    SELECT 1 FROM factions f WHERE f.id = r.entity_a_id AND f.novel_id = n.id
                ))
                OR (r.entity_a_type = 'object' AND EXISTS (
                    SELECT 1 FROM objects o WHERE o.id = r.entity_a_id AND o.novel_id = n.id
                ))
            )
            {chapter_filter}
            ORDER BY ch.number NULLS LAST, r.created_at
            """,
            tuple(params),
            dict_rows=True,
        )

        return {
            "nodes": nodes,
            "edges": [
                {
                    "id": str(edge["id"]),
                    "source": str(edge["entity_a_id"]),
                    "source_type": edge["entity_a_type"],
                    "target": str(edge["entity_b_id"]),
                    "target_type": edge["entity_b_type"],
                    "rel_type": edge["rel_type"],
                    "status": edge["status"],
                    "chapter_number": edge["chapter_number"],
                    "notes": edge["notes"],
                }
                for edge in edges
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
