from __future__ import annotations

import argparse
import json
from typing import Any

from pipeline.db.client import DBClient


def list_timeline(novel_id: str) -> list[dict[str, Any]]:
    with DBClient() as db:
        rows = db.fetchall(
            """
            SELECT t.id, t.description, t.story_date, t.sort_order,
                   t.involved_characters, t.involved_locations,
                   t.involved_objects, t.involved_factions
            FROM timeline t
            WHERE t.novel_id = %s
            ORDER BY t.sort_order, t.created_at
            """,
            (novel_id,),
            dict_rows=True,
        )

        char_rows = db.fetchall(
            "SELECT id, name FROM characters WHERE novel_id = %s", (novel_id,), dict_rows=True
        )
        char_name = {str(r["id"]): r["name"] for r in char_rows}

        result = []
        for r in rows:
            result.append(
                {
                    "id": str(r["id"]),
                    "description": r["description"],
                    "story_date": r["story_date"],
                    "sort_order": r["sort_order"],
                    "involved_characters": [char_name.get(str(c), str(c)) for c in (r["involved_characters"] or [])],
                }
            )
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="List story timeline")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    entries = list_timeline(args.novel_id)

    if args.format == "json":
        print(json.dumps(entries, indent=2, default=str))
        return

    lines = ["# Timeline", ""]
    for e in entries:
        date_str = f" ({e['story_date']})" if e["story_date"] else ""
        chars = ", ".join(e["involved_characters"])
        chars_str = f" — {chars}" if chars else ""
        lines.append(f"- {e['description']}{date_str}{chars_str}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
