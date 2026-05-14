from __future__ import annotations

import argparse
import json
from typing import Any

from pipeline.db.client import DBClient


def build_thread_tracker(
    novel_id: str,
    *,
    status: str | None = None,
    thread_type: str | None = None,
) -> dict[str, Any]:
    with DBClient() as db:
        where = ["pt.novel_id = %s"]
        params: list[Any] = [novel_id]

        if status:
            where.append("pt.status = %s")
            params.append(status)

        if thread_type:
            where.append("pt.thread_type = %s")
            params.append(thread_type)

        where_sql = " AND ".join(where)

        threads = db.fetchall(
            f"""
            SELECT pt.id,
                   pt.title,
                   pt.description,
                   pt.status,
                   pt.thread_type,
                   pt.opened_chapter,
                   pt.closed_chapter
            FROM plot_threads pt
            WHERE {where_sql}
            ORDER BY pt.opened_chapter NULLS LAST, pt.title
            """,
            tuple(params),
            dict_rows=True,
        )

        thread_payload: list[dict[str, Any]] = []
        for thread in threads:
            events = db.fetchall(
                """
                SELECT te.impact,
                       e.id AS event_id,
                       e.description,
                       e.event_type,
                       e.impact_level,
                       ch.number AS chapter_number
                FROM thread_events te
                JOIN events e ON e.id = te.event_id
                JOIN chapters ch ON ch.id = e.chapter_id
                WHERE te.thread_id = %s
                ORDER BY ch.number ASC, e.created_at ASC
                """,
                (thread["id"],),
                dict_rows=True,
            )

            thread_payload.append(
                {
                    **dict(thread),
                    "events": [dict(event) for event in events],
                }
            )

        unresolved_flags = db.fetchall(
            """
            SELECT cf.id,
                   cf.description,
                   cf.flag_type,
                   ch.number AS chapter_number
            FROM continuity_flags cf
            JOIN chapters ch ON ch.id = cf.chapter_id
            WHERE ch.novel_id = %s
              AND cf.resolved = false
            ORDER BY ch.number ASC, cf.created_at ASC
            """,
            (novel_id,),
            dict_rows=True,
        )

        return {
            "threads": thread_payload,
            "unresolved_continuity_flags": [dict(row) for row in unresolved_flags],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build plot thread tracker")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--status")
    parser.add_argument("--thread-type")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    payload = build_thread_tracker(
        args.novel_id,
        status=args.status,
        thread_type=args.thread_type,
    )

    if args.format == "json":
        print(json.dumps(payload, indent=2, default=str))
        return

    lines = ["# Plot Threads", ""]
    for thread in payload["threads"]:
        lines.append(f"- {thread['title']} ({thread['status']})")
    lines.append("")
    lines.append(f"Unresolved continuity flags: {len(payload['unresolved_continuity_flags'])}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
